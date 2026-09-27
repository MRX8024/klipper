# Support for loading code into an mcu at run-time
#
# Copyright (C) 2026  Maksim Bolgov <maksim8024@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import re
import mcu

class MCUModule:
    def __init__(self, config):
        printer = config.get_printer()
        name = config.get_name().split(None, 1)[-1]
        mcu_name = config.get('mcu', 'mcu')
        sources = [s for s in re.split(r'[\s,]+', config.get('sources'))
                   if s]
        if not sources:
            raise config.error("No sources specified for '%s'"
                               % (config.get_name(),))
        try:
            target_mcu = mcu.get_printer_mcu(printer, mcu_name)
        except printer.config_error:
            raise config.error("Unknown mcu '%s' in '%s'"
                               % (mcu_name, config.get_name()))
        target_mcu.register_loadable_module(name, sources)

def load_config_prefix(config):
    return MCUModule(config)
