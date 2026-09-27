# Support for loading code into a micro-controller at run-time
#
# Copyright (C) 2026  Maksim Bolgov <maksim8024@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, re, json, zlib, struct, logging, subprocess, multiprocessing
import msgproto

KLIPPER_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..'))
BUILD_DIR = '~/.cache/klipper/modules'

# Definitions from src/loader.h and src/loader.c
LOADER_MAGIC = 0x444f4d4b
LOADER_ABI_VERSION = 1
LS_ACTIVE = 2
WRITE_CHUNK_SIZE = 48
# Pseudo symbol of scripts/loaderabi.py for the types used by module glue
LOADER_CORE_SYMBOL = '_loader_core'

def read_file(filename, mode='rb'):
    f = open(filename, mode)
    data = f.read()
    f.close()
    return data

# Write a file only if its content changed (so "make" can skip work)
def write_if_changed(filename, data):
    if not isinstance(data, bytes):
        data = data.encode()
    try:
        if read_file(filename) == data:
            return False
    except (IOError, OSError):
        pass
    f = open(filename, 'wb')
    f.write(data)
    f.close()
    return True

# Build, upload, and register the run-time loaded code of an mcu
class MCUModuleHelper:
    def __init__(self, mcu, conn_helper):
        self._printer = printer = mcu.get_printer()
        self._mcu = mcu
        self._conn_helper = conn_helper
        self._serial = conn_helper.get_serial()
        self._reactor = printer.get_reactor()
        self._name = mcu.get_name()
        self._modules = {}
        self._abi_mismatch = []
        # Separate directory for each mcu of each Klipper instance
        config_file = printer.get_start_args().get('config_file', '')
        instance_id = zlib.crc32(os.path.abspath(config_file).encode())
        self._build_dir = os.path.join(
            os.path.expanduser(BUILD_DIR), "%s-%08x" % (
                re.sub(r'[^A-Za-z0-9_.-]', '_', self._name),
                instance_id & 0xffffffff))
        self._log_filename = os.path.join(self._build_dir, 'build.log')
        printer.register_event_handler("klippy:mcu_identify",
                                       self._mcu_identify)
    def register_module(self, name, sources):
        paths = []
        for src in sources:
            path = os.path.normpath(os.path.join(KLIPPER_DIR,
                                                 os.path.expanduser(src)))
            if not os.path.isfile(path):
                raise self._printer.config_error(
                    "Module '%s' source file '%s' not found" % (name, path))
            if re.search(r'\s', path):
                raise self._printer.config_error(
                    "Module '%s' source path '%s' may not contain spaces"
                    % (name, path))
            paths.append(path)
        self._modules[name] = paths
    # Module building
    def _run_build(self, cmd, env=None):
        logging.info("MCU '%s' module build: %s", self._name, " ".join(cmd))
        logfile = open(self._log_filename, 'ab')
        try:
            proc = subprocess.Popen(cmd, cwd=KLIPPER_DIR, stdout=logfile,
                                    stderr=subprocess.STDOUT, env=env)
        except OSError as e:
            logfile.close()
            raise self._mcu.error("Unable to run '%s' to build MCU '%s'"
                                  " module: %s" % (cmd[0], self._name, e))
        while proc.poll() is None:
            self._reactor.pause(self._reactor.monotonic() + 0.050)
        logfile.close()
        if proc.returncode:
            log = read_file(self._log_filename).decode('utf-8', 'replace')
            log = log.strip().split('\n')
            raise self._mcu.error(
                "Build of MCU '%s' loadable module failed (see %s):\n%s"
                % (self._name, self._log_filename, "\n".join(log[-15:])))
    # Generate .config and autoconf.h (only rewritten if changed)
    def _configure(self, kconfig, config_filename, outdir, stamp_filename):
        if os.path.exists(stamp_filename):
            os.remove(stamp_filename)
        write_if_changed(config_filename, kconfig)
        env = dict(os.environ)
        env['KCONFIG_CONFIG'] = config_filename
        self._run_build([sys.executable, 'lib/kconfiglib/olddefconfig.py',
                         'src/Kconfig'], env=env)
        env['KCONFIG_AUTOHEADER'] = os.path.join(outdir, 'autoconf.h')
        self._run_build([sys.executable, 'lib/kconfiglib/genconfig.py',
                         'src/Kconfig'], env=env)
        # Recreate the board/ links
        board_link = os.path.join(outdir, 'board-link')
        if os.path.exists(board_link):
            os.remove(board_link)
        open(stamp_filename, 'w').close()
    def _build(self, fwdict, raw_dictionary, link_info, sources):
        builddir = self._build_dir
        outdir = os.path.join(builddir, 'out') + '/'
        moddir = os.path.join(outdir, 'module')
        try:
            if not os.path.isdir(moddir):
                os.makedirs(moddir)
            open(self._log_filename, 'wb').close()
        except (IOError, OSError) as e:
            raise self._mcu.error("Unable to create MCU '%s' module build"
                                  " directory %s: %s" % (self._name,
                                                         builddir, e))
        # Recreate the firmware build configuration from its data dictionary
        kconfig = fwdict.get('kconfig')
        if kconfig is None:
            raise self._mcu.error("MCU '%s' data dictionary does not contain"
                                  " the build configuration" % (self._name,))
        config_filename = os.path.join(builddir, '.config')
        defconfig_filename = os.path.join(builddir, 'defconfig')
        stamp_filename = os.path.join(builddir, 'config.stamp')
        kconfig_mtime = max([
            os.path.getmtime(os.path.join(d, f))
            for d, dirs, files in os.walk(os.path.join(KLIPPER_DIR, 'src'))
            for f in files if f == 'Kconfig'])
        if (write_if_changed(defconfig_filename, kconfig)
            or not os.path.exists(stamp_filename)
            or os.path.getmtime(stamp_filename) < kconfig_mtime):
            self._configure(kconfig, config_filename, outdir, stamp_filename)
        # Write module build inputs and invoke make
        write_if_changed(os.path.join(moddir, 'sources.txt'),
                         "\n".join(sources) + "\n")
        write_if_changed(os.path.join(moddir, 'firmware.dict'),
                         raw_dictionary)
        write_if_changed(os.path.join(moddir, 'loader.json'),
                         json.dumps(link_info, sort_keys=True))
        image_filename = os.path.join(moddir, 'module.bin')
        old_mtime = None
        if os.path.exists(image_filename):
            old_mtime = os.path.getmtime(image_filename)
        # .config is maintained by _configure()
        self._run_build(['make', '-j%d' % (multiprocessing.cpu_count(),),
                         '-o', config_filename,
                         'KCONFIG_CONFIG=' + config_filename, 'OUT=' + outdir,
                         'MODULE_SRCS=' + " ".join(sources), 'module'])
        if os.path.getmtime(image_filename) != old_mtime:
            logging.info("MCU '%s' loadable module rebuilt (%d bytes)",
                         self._name, os.path.getsize(image_filename))
        else:
            logging.info("MCU '%s' loadable module up to date", self._name)
        host_abi = json.loads(read_file(os.path.join(
            outdir, 'loader_abi.json'), 'r'))
        imports = json.loads(read_file(os.path.join(
            moddir, 'module_imports.json'), 'r'))
        self._check_abi(fwdict, host_abi, imports)
        image = read_file(image_filename)
        moddict = json.loads(read_file(os.path.join(moddir, 'module.json'),
                                       'r'))
        return image, moddict
    # Verify the firmware provides the interface the module was built for
    def _check_abi(self, fwdict, host_abi, imports):
        fw_abi = fwdict.get('loader_abi', {})
        fw_symbols = dict(zip(fwdict.get('loader_exports', []),
                              fw_abi.get('hashes', [])))
        fw_symbols[LOADER_CORE_SYMBOL] = fw_abi.get('core')
        host_symbols = host_abi.get('symbols', {})
        if fw_abi.get('version') != host_abi.get('version'):
            mismatch = ['(interface description missing or outdated)']
        else:
            symbols = [LOADER_CORE_SYMBOL] + imports
            mismatch = [s for s in symbols
                        if fw_symbols.get(s) != host_symbols.get(s)]
        if not mismatch:
            return
        self._abi_mismatch = mismatch
        raise msgproto.error(
            "MCU '%s' firmware interface does not match the host software"
            " used to build its loadable modules (changed: %s)"
            % (self._name, ", ".join(mismatch)))
    def get_status(self):
        if self._abi_mismatch:
            return {'loader_abi_mismatch': list(self._abi_mismatch)}
        return {}
    # Communication with the mcu loader (src/loader.c)
    def _query_state(self):
        query_cmd = self._mcu.lookup_query_command(
            "loader_query", "loader_state state=%c avail=%u image_size=%u"
            " image_crc=%u base=%*s")
        return query_cmd.send()
    def _query_link_info(self, fwdict, state):
        exports_cmd = self._mcu.lookup_query_command(
            "loader_query_exports offset=%hu",
            "loader_exports offset=%hu count=%hu data=%*s")
        names = fwdict['loader_exports']
        addrs = []
        while len(addrs) < len(names):
            params = exports_cmd.send([len(addrs)])
            data = params['data']
            if (params['offset'] != len(addrs) or not data or len(data) % 4
                or params['count'] != len(names)):
                raise self._mcu.error("MCU '%s' invalid loader_exports"
                                      " response" % (self._name,))
            addrs.extend(struct.unpack('<%dI' % (len(data) // 4,), data))
        base, = struct.unpack('<I', state['base'])
        return {'base': base, 'exports': dict(zip(names, addrs))}
    def _check_can_load(self, state):
        get_config_cmd = self._mcu.lookup_query_command(
            "get_config",
            "config is_config=%c crc=%u is_shutdown=%c move_count=%hu")
        config_params = get_config_cmd.send()
        if config_params['is_shutdown']:
            raise self._mcu.error("Can not load module into MCU '%s' as it"
                                  " is shutdown" % (self._name,))
        if config_params['is_config']:
            restart_helper = self._conn_helper.get_restart_helper()
            restart_helper.check_restart_on_module_change()
            raise self._mcu.error("MCU '%s' is already configured - a"
                                  " FIRMWARE_RESTART is required to load"
                                  " modules" % (self._name,))
        if not state['avail']:
            raise self._mcu.error("MCU '%s' has no memory for loadable"
                                  " modules" % (self._name,))
    def _upload(self, state, image, mem_size):
        crc = zlib.crc32(image) & 0xffffffff
        if state['state'] == LS_ACTIVE:
            if state['image_crc'] == crc and state['image_size'] == len(image):
                logging.info("MCU '%s' loadable module already active",
                             self._name)
                return
            restart_helper = self._conn_helper.get_restart_helper()
            restart_helper.check_restart_on_module_change()
            raise self._mcu.error("MCU '%s' is running a different loadable"
                                  " module - a FIRMWARE_RESTART is required"
                                  % (self._name,))
        if mem_size > state['avail']:
            raise self._mcu.error(
                "Loadable modules need %d bytes of ram, but only %d bytes"
                " are available on MCU '%s'" % (mem_size, state['avail'],
                                                self._name))
        begin_cmd = self._mcu.lookup_command("loader_begin size=%u mem_size=%u")
        write_cmd = self._mcu.lookup_command("loader_write offset=%u data=%*s")
        finish_cmd = self._mcu.lookup_command("loader_finish crc=%u")
        begin_cmd.send([len(image), mem_size])
        for pos in range(0, len(image), WRITE_CHUNK_SIZE):
            write_cmd.send([pos, image[pos:pos+WRITE_CHUNK_SIZE]])
        finish_cmd.send([crc])
        state = self._query_state()
        if (state['state'] != LS_ACTIVE or state['image_crc'] != crc
            or self._conn_helper.is_shutdown()):
            raise self._mcu.error("Unable to load module into MCU '%s': %s"
                                  % (self._name,
                                     self._conn_helper.get_shutdown_msg()))
        logging.info("Loaded module into MCU '%s' (%d bytes of code and"
                     " data, %d bytes of ram)", self._name, len(image),
                     mem_size)
    # Return the amount of mcu memory (code, data, and bss) of an image
    def _check_image(self, image, link_info):
        if len(image) < 24:
            raise self._mcu.error("Invalid module image")
        magic, abi, base, image_end, bss_start, bss_end = \
            struct.unpack_from('<6I', image)
        if (magic != LOADER_MAGIC or abi != LOADER_ABI_VERSION
            or base != link_info['base'] or image_end - base != len(image)
            or bss_end < image_end):
            raise self._mcu.error("Invalid module image")
        return bss_end - base
    def _mcu_identify(self):
        if not self._modules:
            return
        sources = sorted(set([path for paths in self._modules.values()
                              for path in paths]))
        msgparser = self._serial.get_msgparser()
        raw_dictionary = msgparser.get_raw_data_dictionary()
        fwdict = json.loads(raw_dictionary)
        if 'loader_exports' not in fwdict:
            raise self._mcu.error("MCU '%s' firmware does not support"
                                  " loadable modules" % (self._name,))
        state = None
        if self._mcu.is_fileoutput():
            # Only the message ids are needed in debugging mode
            link_info = {'base': 0,
                    'exports': {n: 0 for n in fwdict['loader_exports']}}
        else:
            state = self._query_state()
            if state['state'] != LS_ACTIVE:
                self._check_can_load(state)
            link_info = self._query_link_info(fwdict, state)
        try:
            image, moddict = self._build(fwdict, raw_dictionary, link_info,
                                         sources)
        except (IOError, OSError, ValueError) as e:
            raise self._mcu.error("Unable to build MCU '%s' loadable module:"
                                  " %s" % (self._name, e))
        if state is not None:
            mem_size = self._check_image(image, link_info)
            self._upload(state, image, mem_size)
        msgparser.add_data_dictionary(moddict)
