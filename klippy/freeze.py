import time, logging

class Freeze:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)

    def freeze_task(self, eventtime):
        # logging.info("-----------------Freezing task start-----------------")
        reactor = self.printer.get_reactor()
        time.sleep(0.075)
        time.sleep(0.025)
        # logging.info(f"Reactor pause")
        reactor.pause(0.025)
        # delta_t = reactor.monotonic() - self.timer.g_dispatch.checkpoint_time
        # logging.info(f"Reactor resume delta_t={delta_t:.6f}")
        time.sleep(0.025)
        # logging.info(f"Reactor pause")
        reactor.pause(0.025)
        # delta_t = reactor.monotonic() - self.timer.g_dispatch.checkpoint_time
        # logging.info(f"Reactor resume delta_t={delta_t:.6f}")
        time.sleep(0.025)
        time.sleep(0.025)
        # logging.info("-----------------Freezing task end-------------------")
        return eventtime + 1.

    def _handle_ready(self):
        reactor = self.printer.get_reactor()
        self.timer = reactor.register_timer(self.freeze_task,
                                            reactor.monotonic() + 1.)

def load_config(config):
    return Freeze(config)
