"""Bridge the Booster K1 to the local realtime voice agent (dev tool, not HAL).

K1 mic (Booster audio service) --16 kHz mono int16--> agent websocket /ws
agent reply PCM (24 kHz mono int16) --resample 16 kHz--> K1 speaker (AudioPlayer)

The agent (realtime-voice-agent/server.py) runs VAD, turn detection, ASR, the
LLM and TTS. This bridge only moves audio. While the robot is speaking, mic
audio is replaced by silence (half duplex): Booster's echo-cancelled stream is
not available on firmware 1.7.1.1, so the robot would otherwise hear itself.

Run with HAL's venv (it has the Booster SDK and aiohttp):
  BOOSTER_NET_IFACE=enx00e04cfbda07 PYTHONPATH=. hal/.venv/bin/python \\
      robots/booster-k1/tools/k1_voice_bridge.py --minutes 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from hal.drivers.voice._internal.booster_mic import BoosterMicStream, shared_capture  # noqa: E402

log = logging.getLogger("k1-bridge")

AGENT_SR = 24000
K1_SR = 16000
MIC_BLOCK = 512  # 32 ms at 16 kHz
# Keep the mic muted this long after the last reply audio finishes playing.
ECHO_TAIL_S = 0.4


class K1Speaker:
    """Stream 16 kHz mono int16 PCM to the K1 speaker through Booster's player."""

    def __init__(self, volume: float):
        import booster_robotics_sdk_python as sdk

        from hal.drivers.motors.booster_service import init_booster_channel

        # Bind DDS to BOOSTER_NET_IFACE before any SDK client exists.
        init_booster_channel()
        self._sdk = sdk
        self._manager = sdk.AudioManager()
        self._manager.Init()
        self._player = None
        self._volume = volume
        # Monotonic time when everything pushed so far has finished playing.
        self.busy_until = 0.0

    def _ensure(self):
        if self._player is not None:
            return
        opts = self._sdk.PlayerInitOptions()
        opts.source_type = self._sdk.AudioSourceType.kPcmStream
        opts.sample_rate_hz = K1_SR
        opts.channels = 1
        opts.bits_per_sample = 16
        opts.priority = self._sdk.PlayerPriority.kHigh
        player = self._manager.CreatePlayer()
        player.Init(opts)
        player.SetVolume(self._volume)
        player.Start()
        self._player = player
        log.info("speaker stream started (16 kHz mono, volume %.2f)", self._volume)

    def push(self, pcm16k: np.ndarray) -> None:
        self._ensure()
        self._player.PushPcmStream(pcm16k.astype("<i2").tobytes())
        now = time.monotonic()
        self.busy_until = max(self.busy_until, now) + pcm16k.size / K1_SR

    def interrupt(self) -> None:
        """Drop queued reply audio (user barged in)."""
        player, self._player = self._player, None
        self.busy_until = 0.0
        if player is not None:
            for fn in (player.Stop, player.Destroy):
                try:
                    fn()
                except Exception as e:
                    log.debug("player %s failed: %s", fn.__name__, e)

    def close(self) -> None:
        self.interrupt()
        try:
            self._manager.Shutdown()
        except Exception:
            pass


def resample_24k_to_16k(pcm: np.ndarray) -> np.ndarray:
    """Linear 3:2 resample; adequate for speech playback."""
    if pcm.size == 0:
        return pcm
    x = pcm.astype(np.float32)
    n_out = int(round(x.size * K1_SR / AGENT_SR))
    t = np.linspace(0, x.size - 1, n_out, dtype=np.float32)
    return np.clip(np.interp(t, np.arange(x.size, dtype=np.float32), x), -32768, 32767).astype(np.int16)


async def run(url: str, minutes: float, volume: float, gain: float) -> None:
    import aiohttp

    speaker = K1Speaker(volume)
    mic = BoosterMicStream(K1_SR, 1, MIC_BLOCK, np)
    stop_at = time.monotonic() + minutes * 60
    silence = np.zeros(MIC_BLOCK, dtype="<i2").tobytes()
    stats = {"turns": 0, "replies": 0}

    async with aiohttp.ClientSession() as http, http.ws_connect(url, max_msg_size=0) as ws:
        log.info("connected to agent %s; talk to the robot (%.0f min)", url, minutes)

        async def uplink():
            loop = asyncio.get_running_loop()
            sent = 0
            peak_rms = 0.0
            next_report = time.monotonic() + 5
            with mic:
                while time.monotonic() < stop_at and not ws.closed:
                    data, _ = await loop.run_in_executor(None, mic.read, MIC_BLOCK)
                    x = data[:, 0].astype(np.float32) * gain
                    pcm = np.clip(x, -32768, 32767).astype("<i2")
                    muted = time.monotonic() < speaker.busy_until + ECHO_TAIL_S
                    await ws.send_bytes(silence if muted else pcm.tobytes())
                    sent += 1
                    peak_rms = max(peak_rms, float(np.sqrt(np.mean(x * x))))
                    if time.monotonic() >= next_report:
                        log.info("mic: %d blocks sent, peak RMS %.0f (after gain x%.1f)%s",
                                 sent, peak_rms, gain, " [muted: robot speaking]" if muted else "")
                        sent, peak_rms = 0, 0.0
                        next_report = time.monotonic() + 5

        reply: list[str] = []

        async def downlink():
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    pcm = np.frombuffer(msg.data, dtype="<i2")
                    speaker.push(resample_24k_to_16k(pcm))
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    ev = json.loads(msg.data)
                    kind = ev.get("type")
                    if kind != "agent_delta":
                        log.info("event: %s", ev)
                    if kind == "user":
                        stats["turns"] += 1
                        log.info("USER : %s", ev.get("text"))
                    elif kind == "agent_delta":
                        reply.append(ev.get("text", ""))
                    elif kind == "agent_end":
                        stats["replies"] += 1
                        log.info("ROBOT: %s", "".join(reply).strip())
                        reply.clear()
                    elif kind == "metrics":
                        log.info("timing: %s", {k: v for k, v in ev.items() if k != "type"})
                    elif kind == "interrupt":
                        if time.monotonic() < speaker.busy_until:
                            speaker.interrupt()
                            log.info("(barge-in: reply cut)")
                    elif kind == "status":
                        log.info("agent: %s", ev.get("text"))
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

        up = asyncio.create_task(uplink())
        down = asyncio.create_task(downlink())
        try:
            done, _ = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception() is not None:
                    log.error("%s stopped with an error: %r",
                              "uplink" if t is up else "downlink", t.exception())
        finally:
            for t in (up, down):
                t.cancel()
            await ws.close()
            speaker.close()
            shared_capture(np).close()
            log.info("bridge stopped: %d user turns, %d replies", stats["turns"], stats["replies"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="ws://127.0.0.1:8090/ws")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--volume", type=float, default=0.5, help="K1 player volume 0..1")
    ap.add_argument("--gain", type=float, default=4.0, help="mic gain before sending to the agent")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args.url, args.minutes, args.volume, args.gain))


if __name__ == "__main__":
    main()
