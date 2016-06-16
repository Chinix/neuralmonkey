"""
This module is responsible for loading training configuration.
"""

import time
import traceback
import collections
from inspect import isfunction, isclass, getargspec
import importlib
import regex as re
from utils import log


OBJECT_NAME = re.compile(r"^\[([a-zA-Z][a-zA-Z0-9_]*)\]$")
OBJECT_REF = re.compile(r"^<([a-zA-Z][a-zA-Z0-9_]*)>$")
KEY_VALUE_PAIR = re.compile(r"^([a-zA-Z][a-zA-Z0-9_]*) *= *(.+)$")
INTEGER = re.compile(r"^[0-9]+$")
FLOAT = re.compile(r"^[0-9]*\.[0-9]*(e[+-]?[0-9]+)?$")
LIST = re.compile(r"\[([^]]*)\]")
TUPLE = re.compile(r"\(([^]]+)\)")
CLASS_NAME = re.compile(r"^_*[a-zA-Z][a-zA-Z0-9_]*(\._*[a-zA-Z][a-zA-Z0-9_]*)+$")

def split_on_commas(string):
    """
    This is a clever splitter a bracketed string on commas.
    """
    items = []
    char_buffer = []
    openings = []
    for i, char in enumerate(string):
        if char == ',' and len(openings) == 0:
            items.append("".join(char_buffer))
            char_buffer = []
            continue
        elif char == ' ' and len(char_buffer) == 0:
            continue
        elif char == '(' or char == '[':
            openings.append(char)
        elif char == ')':
            if openings.pop() != '(':
                raise Exception('Invalid bracket end ")", col {}.'.format(i))
        elif char == ']':
            if openings.pop() != '[':
                raise Exception('Invalid bracket end "]", col {}.'.format(i))
        char_buffer.append(char)
    items.append("".join(char_buffer))
    return items


def format_value(string):
    #pylint: disable=too-many-return-statements,too-many-branches
    """ Parses value from the INI file: int/float/string/object """
    if string == 'False':
        return False
    elif string == 'True':
        return True
    elif string == 'None':
        return None
    elif INTEGER.match(string):
        return int(string)
    elif FLOAT.match(string):
        return float(string)
    elif CLASS_NAME.match(string):
        class_parts = string.split(".")
        class_name = class_parts[-1]
        module_name = ".".join(class_parts[:-1])
        try:
            module = importlib.import_module(module_name)
        except:
            raise Exception(("Interpretation \"{}\" as type name, module \"{}\" "+
                             "does not exist. Did you mean file \"./{}\"?")\
                                     .format(string, module_name, string))
        try:
            clazz = getattr(module, class_name)
        except:
            raise Exception(("Interpretation \"{}\" as type name, class \"{}\" "+
                             "does not exist. Did you mean file \"./{}\"?")\
                                     .format(string, class_name, string))
        return clazz
    elif OBJECT_REF.match(string):
        return "object:"+OBJECT_REF.match(string)[1]
    elif LIST.match(string):
        matched_content = LIST.match(string)[1]
        if matched_content == '':
            return []
        items = split_on_commas(matched_content)
        values = [format_value(val) for val in items]
        types = [type(val) for val in values]
        if len(set(types)) > 1:
            raise Exception("List must of a same type, is: {}".format(types))
        return values
    elif TUPLE.match(string):
        items = split_on_commas(TUPLE.match(string)[1])
        values = [format_value(val) for val in items]
        return tuple(values)
    else:
        return string


def get_config_dicts(config_file):
    """ Parses the INI file into a dictionary """
    config_dicts = dict()
    time_stamp = time.strftime("%Y-%m-%d-%H-%M-%S")

    current_name = None
    for i, line in enumerate(config_file):
        try:
            line = line.strip()
            line = re.sub(r"#.*", "", line)
            line = re.sub(r"\$TIME", time_stamp, line)
            if not line:
                pass
            elif line.startswith(";"):
                pass
            elif OBJECT_NAME.match(line):
                current_name = OBJECT_NAME.match(line)[1]
                if current_name in config_dicts:
                    raise Exception("Duplicit object key: '{}', line {}.".format(current_name, i))
                config_dicts[current_name] = dict()
            elif KEY_VALUE_PAIR.match(line):
                matched = KEY_VALUE_PAIR.match(line)
                key = matched[1]
                value_string = matched[2]
                if key in config_dicts[current_name]:
                    raise Exception("Duplicit key in '{}' object, line {}.".format(key, i))
                config_dicts[current_name][key] = format_value(value_string)
            else:
                raise Exception("Unknown string: \"{}\"".format(line))
        except Exception as exc:
            log("Syntax error on line {}: {}".format(i, exc.message), color='red')
            exit(1)

    config_file.close()
    return config_dicts


def get_object(value, all_dicts, existing_objects, depth):
    """
    Constructs an object from dict with its arguments. It works recursively.

    Args:

        value: A value that should be resolved (either a singular value or
            object name)

        all_dicts: Raw configuration dictionaries. It is used to find configuration
            of unconstructed objects.

        existing_objects: A dictionary for keeping already constructed objects.

        depth: Current depth of recursion. Used to prevent an infinite recursion.

    """
    if not isinstance(value, basestring) and isinstance(value, collections.Iterable):
        return [get_object(val, all_dicts, existing_objects, depth + 1)
                for val in value]
    if value in existing_objects:
        return existing_objects[value]
    if not isinstance(value, basestring) or not value.startswith("object:"):
        return value

    name = value[7:]
    if name not in all_dicts:
        raise Exception("Object \"{}\" was not defined in the configuration.".format(name))
    this_dict = all_dicts[name]

    if depth > 20:
        raise Exception("Configuration does also object depth more thatn 20.")
    if 'class' not in this_dict:
        raise Exception("Class is not defined for object: {}".format(name))

    clazz = this_dict['class']

    if not isclass(clazz) and not isfunction(clazz):
        raise Exception(("The \"class\" field with value \"{}\" in object \"{}\""+
                         " should be a type or function, was").format(clazz, name, type(clazz)))

    def process_arg(arg):
        """ Resolves potential references to other objects """
        return get_object(arg, all_dicts, existing_objects, depth + 1)

    args = {k: process_arg(arg) for k, arg in this_dict.iteritems() if k != 'class'}

    func_to_call = clazz.__init__ if isclass(clazz) else clazz
    arg_spec = getargspec(func_to_call)

    # if tha parameters are not passed via a keywords, check whether they match
    if not arg_spec.keywords:
        defaults = arg_spec.defaults if arg_spec.defaults else ()
        if arg_spec.args[0] == 'self':
            required_args = set(arg_spec.args[1:-len(defaults)])
        else:
            required_args = set(arg_spec.args[:-len(defaults)])
        all_args = set(arg_spec.args)
        additional_args = set()

        for key in args.keys():
            if key in required_args:
                required_args.remove(key)
            if key not in all_args:
                additional_args.add(key)

        if required_args:
            raise Exception("Object \"{}\" is missing required args: {}".\
                    format(name, ", ".join(required_args)))
        if additional_args:
            raise Exception("Object \"{}\" got unexpected argument: {}".\
                    format(name, ", ".join(additional_args)))

    try:
        result = clazz(**args)
    except Exception as exc:
        log("Failed to create object \"{}\" of class \"{}.{}\": {}"\
                .format(name, clazz.__module__, clazz.__name__, exc.message), color='red')
        traceback.print_exc()
        exit(1)
    existing_objects[value] = result
    return result


def load_config_file(config_file):
    """ Loads the complete configuration of an experiment. """
    config_dicts = get_config_dicts(config_file)
    log("INI file is parsed.")

    # first load the configuration into a dictionary

    if "main" not in config_dicts:
        raise Exception("Configuration does not contain the main block.")

    existing_objects = dict()

    main_config = config_dicts['main']

    configuration = dict()
    for key, value in main_config.iteritems():
        try:
            configuration[key] = get_object(value, config_dicts,
                                            existing_objects, 0)
        except Exception as exc:
            log("Error while loading {}: {}".format(key, exc.message), color='red')
            traceback.print_exc()
            exit(1)

    return configuration
