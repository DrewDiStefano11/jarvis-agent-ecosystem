import time
from .supervisor import Supervisor

class Watchdog:
    def __init__(self, supervisor: Supervisor):
        self.supervisor = supervisor
        self.running = False

    def start(self):
        self.running = True
        self.loop()

    def stop(self):
        self.running = False

    def loop(self):
        while self.running:
            self.supervisor.tick()
            time.sleep(self.supervisor.config.watchdog_interval_seconds)
