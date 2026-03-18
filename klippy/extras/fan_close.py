PIN_MIN_TIME = 0.010


class FanClose:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.printer.register_event_handler("klippy:connect", self._handle_connect)
        self.gcode.register_command('FAN_SWITCH_F', self.cmd_FAN_SWITCH_F, desc='Fan switch')
        self.gcode.register_command('FAN_CLOSE_F', self.cmd_FAN_CLOSE_F, desc='Fan close')
        self.gcode.register_command('FAN_OPEN_F', self.cmd_FAN_OPEN_F, desc='Fan open')
        self.gcode.register_command('FAN_SWITCH', self.cmd_FAN_SWITCH, desc='Fan switch')
        self.gcode.register_command('FAN_CLOSE', self.cmd_FAN_CLOSE, desc='Fan close')
        self.gcode.register_command('FAN_OPEN', self.cmd_FAN_OPEN, desc='Fan open')
        self.fan_last_speed = .0

    def _handle_connect(self):
        self.fan = self.printer.lookup_object('heater_fan hotend_fan')
        self.fan_def_speed = self.fan.fan_speed

    def cmd_FAN_CLOSE_F(self, gcmd):
        if self.fan.fan_speed:
            self.fan_last_speed = self.fan.fan_speed
            now = self.printer.get_reactor().monotonic()
            print_time = self.fan.fan.get_mcu().estimated_print_time(now)
            self.fan.fan.set_speed(print_time + PIN_MIN_TIME, .0)

    def cmd_FAN_OPEN_F(self, gcmd):
        if self.fan_last_speed:
            now = self.printer.get_reactor().monotonic()
            print_time = self.fan.fan.get_mcu().estimated_print_time(now)
            self.fan.fan.set_speed(print_time + PIN_MIN_TIME, self.fan_last_speed)
            self.fan_last_speed = .0

    def cmd_FAN_SWITCH_F(self, gcmd):
        on = gcmd.get('ON', 'True')
        if on == 'True':
            on = True
        else:
            on = False
        self.gcode.respond_info(f'Value: {on}, type: {str(type(on))}')
        # now = self.printer.get_reactor().monotonic()
        # print_time = self.fan.fan.get_mcu().estimated_print_time(now)
        # self.fan_last_speed, speed = (0.0, self.fan_last_speed)\
        #     if self.fan_last_speed else (self.fan.fan_speed, 0.0)
        # self.fan.fan.set_speed(print_time + PIN_MIN_TIME, speed)
        now = self.printer.get_reactor().monotonic()
        print_time = self.fan.fan.get_mcu().estimated_print_time(now)
        speed = self.fan.last_speed if on else .0
        self.fan.fan.set_speed(print_time + 0.01, speed)

    def cmd_FAN_CLOSE(self, gcmd):
        self.fan_last_speed = self.fan.fan_speed
        self.fan.fan_speed = 0

    def cmd_FAN_OPEN(self, gcmd):
        self.fan.fan_speed = self.fan_last_speed

    def cmd_FAN_SWITCH(self, gcmd):
        self.fan_last_speed, speed = (0.0, self.fan_last_speed) \
            if self.fan_last_speed else (self.fan.fan_speed, 0.0)
        self.fan.fan_speed = speed


def load_config(config):
    return FanClose(config)
