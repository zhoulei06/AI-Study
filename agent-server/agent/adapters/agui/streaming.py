"""Bounded live subscription. Persisted events remain available after overflow."""

import asyncio


class SlowSubscriber(Exception):
    pass


class EventChannel:
    def __init__(self, *, maxsize=256):
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.connected = True

    def publish(self, event, sequence=0):
        if self.connected:
            self.loop.call_soon_threadsafe(self._deliver, event, sequence)

    def _deliver(self, event, sequence):
        if not self.connected:
            return
        try:
            self.queue.put_nowait((sequence, event))
        except asyncio.QueueFull:
            self.connected = False
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait((0, SlowSubscriber()))

    def close(self):
        self.connected = False
