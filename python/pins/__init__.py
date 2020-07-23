"""
The ``pins`` module provides an API to pin, discover and share files.
"""

import os
from _pins_cffi import ffi
import subprocess
import platform
import sys

rlib = None
pins_init = False

def _get_rhome():
    r_home = os.environ.get("R_HOME")
    if r_home:
        return r_home
    tmp = subprocess.check_output(("R", "RHOME"), universal_newlines=True)
    r_home = tmp.split(os.linesep)
    if r_home[0].startswith("WARNING"):
        r_home = r_home[1]
    else:
        r_home = r_home[0].strip()
    return r_home

def _get_rlib():
    r_home = _get_rhome()
    system = platform.system()
    if system == "Linux":
        lib_path = os.path.join(r_home, "lib", "libR.so")
    elif system == "Darwin":
        lib_path = os.path.join(r_home, "lib", "libR.dylib")
    else:
        raise ValueError("System '%s' is unsupported.")
    return lib_path

def _open_rlib():
    return ffi.dlopen(_get_rlib())

def _print(message):
    sys.stdout.write(message)
    sys.stdout.flush()

@ffi.callback("void(char *, int, int)")
def _console_write(buffer, size, otype):
    _print(ffi.string(buffer, size).decode("utf-8"))

@ffi.callback("void(char *)")
def _showmessage(buffer):
    _print(ffi.string(buffer).decode("utf-8"))

@ffi.callback('void(SA_TYPE, int, int)')
def _cleanup(saveact, status, runlast):
    pass

@ffi.callback('void(void)')
def _processevents():
    pass

@ffi.callback('void(int)')
def _busy(which):
    pass
    
def _main_loop_started():
    return rlib.ptr_R_WriteConsoleEx != ffi.NULL or rlib.R_GlobalEnv != ffi.NULL

def r_start():
    global rlib
    if (rlib != None):
        return rlib
        
    os.environ["R_HOME"] = _get_rhome()
    rlib = _open_rlib()

    if (_main_loop_started()):
        return rlib
        
    import atexit
    atexit.register(r_end, 0)
        
    options = ("pins", "--quiet", "--vanilla", "--no-save")
    options_raw = [ffi.new("char[]", o.encode("ASCII")) for o in options]
    status = rlib.Rf_initialize_R(ffi.cast("int", len(options_raw)), options_raw)

    rlib.ptr_R_WriteConsoleEx = _console_write
    rlib.ptr_R_WriteConsole = ffi.NULL
    rlib.ptr_R_CleanUp = _cleanup
    rlib.ptr_R_ProcessEvents = _processevents
    rlib.ptr_R_Busy = _busy

    rlib.setup_Rmainloop()

    return rlib

def r_end(fatal):
    rlib.R_dot_Last()
    rlib.R_RunExitFinalizers()
    rlib.Rf_KillAllDevices()
    rlib.R_CleanTempDir()
    rlib.R_gc()
    rlib.Rf_endEmbeddedR(fatal)

def r_eval(code, environment = None):
    r_start()
    
    cmdSexp = rlib.Rf_allocVector(rlib.STRSXP, 1)
    rlib.Rf_protect(cmdSexp)
    
    ffi_code = ffi.new("char[]", code.encode("ASCII"))
    rlib.SET_STRING_ELT(cmdSexp, 0, rlib.Rf_mkChar(ffi_code));
    
    status = ffi.new("ParseStatus *")
    cmdexpr = rlib.Rf_protect(rlib.R_ParseVector(cmdSexp, -1, status, rlib.R_NilValue))

    rlib.Rf_unprotect(2)
    if status[0] != rlib.PARSE_OK:
        raise RuntimeError("Failed to parse: " + code)

    if environment == None:
        environment = rlib.R_GlobalEnv
        
    error = ffi.new("int *")

    result = rlib.Rf_protect(rlib.R_tryEval(rlib.VECTOR_ELT(cmdexpr, 0), environment, error))

    if (error[0]):
        message = r_eval("gsub('\\\n', '', geterrmessage())")
        raise RuntimeError(message + " at " + code)

    rtype = result.sxpinfo.type
    if (rtype == rlib.CHARSXP):
        result = ffi.string(rlib.R_CHAR(result)).decode("utf-8")
    elif (rtype == rlib.STRSXP):
        result = ffi.string(rlib.R_CHAR(rlib.STRING_ELT(result, 0))).decode("utf-8")
    elif (rtype == rlib.RAWSXP):
        n = rlib.Rf_xlength(result)
        result = ffi.buffer(rlib.RAW(result), n)

    rlib.Rf_unprotect(1)
    return result

def _build_call(function, params):
  call = function + "("
  first = True
  
  for key in params:
    if not first:
      call = call + ", "
      
    if params[key] == None:
      continue
      
    if type(params[key]) is dict:
      call = call + key + " = " + params[key]["code"]
    else:
      call = call + key + " = \"" + params[key] + "\""
      
    first = False
  
  call = call + ")"
  return call
  
def _init_pins():
  global pins_init
  
  if not pins_init:
    r_start()
    pins_installed = r_eval("as.character(length(find.package('pins', quiet = TRUE)) > 0)")
    feather_installed = r_eval("as.character(length(find.package('feather', quiet = TRUE)) > 0)")
    
    if pins_installed != "TRUE":
      r_eval("install.packages('pins', version = '0.3.1', repos = pins:::packages_repo_default())")
    
    if feather_installed != "TRUE":
      r_eval("install.packages('feather', repos = pins:::packages_repo_default())")
    
    r_eval("library('pins')")
    pins_init = True
    
def _from_arrow(buffer):
    import pyarrow as pa
    return pa.ipc.open_stream(buffer).read_pandas()

def _to_arrow(x):
    import pyarrow as pa
    return pa.ipc.open_stream(buffer).read_pandas()

def _to_feather(x):
    import feather
    feather_path = r_eval('tempfile(fileext = ".feather")')
    feather.write_dataframe(x, feather_path)
    return feather_path
    
def _from_feather(path):
    import feather
    return feather.read_dataframe(path)
    
def _eval_deserialize(operation):
    feather_path = r_eval('tempfile(fileext = ".feather")')
    r_eval("feather::write_feather(pins:::pin_for_python(" + operation + "), \"" + feather_path + "\")")
    result = _from_feather(feather_path)
    os.remove(feather_path)
    return result

def pin_find(text = "", board = None):
    """
    Find Pin.
    """
    _init_pins()
    return _eval_deserialize(_build_call("pins::pin_find", { 'text': text, 'board': board }))

def pin_get(name, board = None):
    """
    Retrieve Pin.
    """
    _init_pins()
    return _eval_deserialize(_build_call("pins::pin_get", { 'name': name, 'board': board }))

def pin(x, name, description = "", board = None):
    """
    Create Pin.
    """
    _init_pins()
    result = None
    
    path = _to_feather(x)
    r_eval(
      "feather::write_feather(" +
      _build_call("pins::pin", {
        'x': { 'code': "feather::read_feather(\"" + path + "\")" },
        'name': name,
        'description': description,
        'board': board }) +
      ", \"" + path + "\")")
    result = _from_feather(path)
    os.remove(path)
      
    return result

def pin_remove(name, board = None):
    """
    Remove Pin.
    """
    _init_pins()
    return _eval_deserialize(_build_call("pins::pin_remove", { 'name': name, 'board': board }))

def board_deregister(name):
    """
    Deregister Board.
    """
    _init_pins()
    r_eval(_build_call("pins::board_deregister", { 'name': name }))

def board_get(name):
    """
    Get Board.
    """
    _init_pins()
    board_call = _build_call("pins::board_get", { 'name': name })
    board_names = "names(" + board_call + ")"
    board_values = "as.character(" + board_call + ")"
    return _eval_deserialize("data.frame(attribute = " + board_names + ", value = " + board_values + ")")

def board_list():
    """
    List Boards.
    """
    _init_pins()
    return _eval_deserialize("data.frame(board = " + _build_call("pins::board_list", { }) + ")")
     
def board_register(board, name = None, **kwargs):
    """
    Register Board.
    """
    _init_pins()
    
    params = { 'board': board, 'name': name }
    params.update(kwargs.items())
        
    r_eval(_build_call("pins::board_register", params))
