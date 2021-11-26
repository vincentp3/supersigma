#!/usr/bin/env python3
# A Sigma to SIEM converter
# Copyright 2016-2017 Thomas Patzke, Florian Roth

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys
import argparse
import yaml
import ruamel.yaml
import json
import pathlib
import itertools
import logging, traceback
from sigma.parser.collection import SigmaCollectionParser
from sigma.parser.exceptions import SigmaCollectionParseError, SigmaParseError
from sigma.configuration import SigmaConfiguration, SigmaConfigurationChain
from sigma.config.collection import SigmaConfigurationManager
from sigma.config.exceptions import SigmaConfigParseError, SigmaRuleFilterParseException
from sigma.filter import SigmaRuleFilter
import sigma.backends.discovery as backends
from sigma.backends.base import BackendOptions
from sigma.backends.exceptions import BackendError, NotSupportedError, PartialMatchError, FullMatchError
from sigma.parser.modifiers import modifiers
import codecs
import copy

sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

# Error codes

ERR_OUTPUT              = 1
ERR_INVALID_YAML        = 3
ERR_SIGMA_PARSING       = 4
ERR_OPEN_SIGMA_RULE     = 5
ERR_OPEN_CONFIG_FILE    = 5
ERR_CONFIG_INVALID_YAML = 6
ERR_CONFIG_PARSING      = 6
ERR_BACKEND             = 8
ERR_NOT_SUPPORTED       = 9
ERR_NO_TARGET           = 10
ERR_RULE_FILTER_PARSING = 11
ERR_CONFIG_REQUIRED     = 20
ERR_CONFIG_ORDER        = 21
ERR_CONFIG_BACKEND      = 22
ERR_OUTPUT_FORMAT       = 30
ERR_NOT_IMPLEMENTED     = 42
ERR_PARTIAL_FIELD_MATCH = 80
ERR_FULL_FIELD_MATCH    = 90

# Allowed fields in output
allowed_fields = ["title", "id", "status", "description", "author", "references", "fields", "falsepositives", "level", "tags", "filename"]

def alliter(path):
    for sub in path.iterdir():
        if sub.name.startswith("."):
            continue
        if sub.is_dir():
            yield from alliter(sub)
        else:
            yield sub

def get_inputs(paths, recursive):
    if paths == ['-']:
        return [sys.stdin]

    if recursive:
        return list(itertools.chain.from_iterable([list(alliter(pathlib.Path(p))) for p in paths]))
    else:
        return [pathlib.Path(p) for p in paths]

class ActionBackendHelp(argparse.Action):
    def __call__(self, parser, ns, vals, opt):
        backend = backends.getBackend(vals)
        if len(backend.options) > 0:
            helptext = "Backend options for " + backend.identifier + "\n"
            for option, default, help, _ in backend.options:
                helptext += "    {:10}: {} (default: {})".format(option, help, default) + "\n"

        print(helptext)
        exit(0)

def set_argparser():
    """Sets up and parses the command line arguments for Sigmac.
    Returns the argparser"""
    argparser = argparse.ArgumentParser(description="Convert Sigma rules into SIEM signatures.")
    argparser.add_argument("--recurse", "-r", action="store_true", help="Use directory as input (recurse into subdirectories is not implemented yet)")
    argparser.add_argument("--filter", "-f", help="""
    Define comma-separated filters that must match (AND-linked) to rule to be processed.
    Valid filters: level<=x, level>=x, level=x, status=y, logsource=z, tag=t, target=o.
    x is one of: low, medium, high, critical.
    y is one of: experimental, testing, stable.
    z is a word appearing in an arbitrary log source attribute.
    t is a tag that must appear in the rules tag list, case-insensitive matching.
    o is a target that must appear in the rules target list, case-insensitive matching.
    Multiple log source specifications are AND linked.
    Special filter:
    inlastday=X rule create or modified in the last X days period
    tlp=valid_tlp if rule have no tlp set to WHITE 
            """)
    argparser.add_argument("--target", "-t", choices=backends.getBackendDict().keys(), help="Output target format")
    argparser.add_argument("--lists", "-l", action="store_true", help="List available output target formats and configurations")
    argparser.add_argument("--config", "-c", action="append", help="Configurations with field name and index mapping for target environment. Multiple configurations are merged into one. Last config is authoritative in case of conflicts.")
    argparser.add_argument("--output", "-o", default=None, help="Output file or filename prefix (if end with a '_','/' or '\\')")
    argparser.add_argument("--output-fields", "-of", help="""Enhance your output with additional fields from the Sigma rule (not only the converted rule itself). 
    Select the fields you want by providing their list delimited with commas (no space). Only work with the '--output-format' option and with 'json' or 'yaml' value.
    available additional fields : title, id, status, description, author, references, fields, falsepositives, level, tags.
    This option do not have any effect for backends that already format output : elastalert, kibana, splukxml etc. """)
    argparser.add_argument("--output-format", "-oF", choices=["json", "yaml"], help="Use only if you want to have JSON or YAML output (default is raw text)")
    argparser.add_argument("--output-extention", "-e", default=None, help="Extension of Output file for filename prefix use")
    argparser.add_argument("--print0", action="store_true", help="Delimit results by NUL-character")
    argparser.add_argument("--backend-option", "-O", action="append", help="Options and switches that are passed to the backend")
    argparser.add_argument("--backend-config", "-C", help="Configuration file (YAML format) containing options to pass to the backend")
    argparser.add_argument("--backend-help", action=ActionBackendHelp, help="Print backend options")
    argparser.add_argument("--defer-abort", "-d", action="store_true", help="Don't abort on parse or conversion errors, proceed with next rule. The exit code from the last error is returned")
    argparser.add_argument("--ignore-backend-errors", "-I", action="store_true", help="Only return error codes for parse errors and ignore errors for rules that cause backend errors. Useful, when you want to get as much queries as possible.")
    argparser.add_argument("--shoot-yourself-in-the-foot", action="store_true", help=argparse.SUPPRESS)
    argparser.add_argument("--verbose", "-v", action="store_true", help="Be verbose")
    argparser.add_argument("--debug", "-D", action="store_true", help="Debugging output")
    argparser.add_argument("inputs", nargs="*", help="Sigma input files ('-' for stdin)")
    
    return argparser

def list_backends(debug):
    for backend in sorted(backends.getBackendList(), key=lambda backend: backend.identifier):
        if debug:
            print("{:>15} : {} ({})".format(backend.identifier, backend.__doc__, backend.__name__))
        else:
            print("{:>15} : {}".format(backend.identifier, backend.__doc__))

def list_configurations(backend=None, scm=None):
    for conf_id, title, backends in sorted(scm.list(), key=lambda config: config[0]):
        if backend is not None and backend in backends or backend is None or len(backends) == 0:
            print("{:>30} : {}".format(conf_id, title))

def list_modifiers(modifiers):
    for modifier_id, modifier in modifiers.items():
        print("{:>10} : {}".format(modifier_id, modifier.__doc__))

def main():
    argparser = set_argparser()
    cmdargs = argparser.parse_args()
    scm = SigmaConfigurationManager()

    logger = logging.getLogger(__name__)
    if cmdargs.debug:   # pragma: no cover
        logging.basicConfig(filename='sigmac.log', filemode='w', level=logging.DEBUG)
        logger.setLevel(logging.DEBUG)

    if cmdargs.lists:
        print("Backends (Targets):")
        list_backends(cmdargs.debug)

        print()
        print("Configurations (Sources):")
        list_configurations(backend=cmdargs.target, scm=scm)

        print()
        print("Modifiers:")
        list_modifiers(modifiers=modifiers)
        sys.exit(0)
    elif len(cmdargs.inputs) == 0:
        print("Nothing to do!")
        argparser.print_usage()
        sys.exit(0)

    if cmdargs.target is None:
        print("No target selected, select one with -t/--target")
        argparser.print_usage()
        sys.exit(ERR_NO_TARGET)

    logger.debug("* Target selected %s" % (cmdargs.target))

    rulefilter = None
    if cmdargs.filter:
        try:
            rulefilter = SigmaRuleFilter(cmdargs.filter)
        except SigmaRuleFilterParseException as e:
            print("Parse error in Sigma rule filter expression: %s" % str(e), file=sys.stderr)
            sys.exit(ERR_RULE_FILTER_PARSING)

    sigmaconfigs = SigmaConfigurationChain()
    backend_class = backends.getBackend(cmdargs.target)
    if cmdargs.config is None:
        if backend_class.config_required and not cmdargs.shoot_yourself_in_the_foot:
            print("The backend you want to use usually requires a configuration to generate valid results. Please provide one with --config/-c.", file=sys.stderr)
            print("Available choices for this backend (get complete list with --lists/-l):")
            list_configurations(backend=cmdargs.target, scm=scm)
            sys.exit(ERR_CONFIG_REQUIRED)
        if backend_class.default_config is not None:
            cmdargs.config = backend_class.default_config

    if cmdargs.config:
        order = 0
        for conf_name in cmdargs.config:
            try:
                sigmaconfig = scm.get(conf_name)
                if sigmaconfig.order is not None:
                    if sigmaconfig.order <= order and not cmdargs.shoot_yourself_in_the_foot:
                        print("The configurations were provided in the wrong order (order key check in config file)", file=sys.stderr)
                        sys.exit(ERR_CONFIG_ORDER)
                    order = sigmaconfig.order

                try:
                    if cmdargs.target not in sigmaconfig.config["backends"]:
                        print("The configuration '{}' is not valid for backend '{}'. Valid choices are: {}".format(conf_name, cmdargs.target, ", ".join(sigmaconfig.config["backends"])), file=sys.stderr)
                        sys.exit(ERR_CONFIG_ORDER)
                except KeyError:
                    pass

                sigmaconfigs.append(sigmaconfig)
            except OSError as e:
                print("Failed to open Sigma configuration file %s: %s" % (conf_name, str(e)), file=sys.stderr)
                exit(ERR_OPEN_CONFIG_FILE)
            except (yaml.parser.ParserError, yaml.scanner.ScannerError) as e:
                print("Sigma configuration file %s is no valid YAML: %s" % (conf_name, str(e)), file=sys.stderr)
                exit(ERR_CONFIG_INVALID_YAML)
            except SigmaConfigParseError as e:
                print("Sigma configuration parse error in %s: %s" % (conf_name, str(e)), file=sys.stderr)
                exit(ERR_CONFIG_PARSING)

    if cmdargs.output_fields:
        if cmdargs.output_format: 
            output_fields_rejected = [field for field in cmdargs.output_fields.split(",") if field not in allowed_fields] # Not allowed fields
            if output_fields_rejected:
                    print("These fields are not allowed (check help for allow field list) : %s" % (", ".join(output_fields_rejected)), file=sys.stderr)
                    exit(ERR_OUTPUT_FORMAT)
            else:
                output_fields_filtered = [field for field in cmdargs.output_fields.split(",") if field in allowed_fields] # Keep only allowed fields
        else:
            print("The '--output-fields' or '-of' arguments must be used with '--output-format' or '-oF' equal to 'json' or 'yaml'", file=sys.stderr)
            exit(ERR_OUTPUT_FORMAT)

    backend_options = BackendOptions(cmdargs.backend_option, cmdargs.backend_config)
    backend = backend_class(sigmaconfigs, backend_options)
    
    filename_ext = cmdargs.output_extention
    filename = cmdargs.output
    fileprefix = None
    if filename:
        if filename_ext:
            if filename_ext[0] == '.':
                pass
            else:
                filename_ext = '.' + filename_ext
        else:
            filename_ext = '.rule'
    
        if filename[-1:] in ['_','/','\\']:
            fileprefix = filename
        else:
            try:
                out = open(filename, "w", encoding='utf-8')
            except (IOError, OSError) as e:
                print("Failed to open output file '%s': %s" % (filename, str(e)), file=sys.stderr)
                exit(ERR_OUTPUT)
    else:
        out = sys.stdout

    error = 0
    output_array = []
    for sigmafile in get_inputs(cmdargs.inputs, cmdargs.recurse):
        logger.debug("* Processing Sigma input %s" % (sigmafile))
        success = True
        try:
            if cmdargs.inputs == ['-']:
                f = sigmafile
            else:
                f = sigmafile.open(encoding='utf-8')
            parser = SigmaCollectionParser(f, sigmaconfigs, rulefilter, sigmafile)
            results = parser.generate(backend)

            nb_result = len(list(copy.deepcopy(results)))
            inc_filenane = None if nb_result < 2 else 0
            
            newline_separator = '\0' if cmdargs.print0 else '\n'

            results = list(results) # Since results is an iterator and used twice we convert it a list
            for result in results:
                if not fileprefix == None and not inc_filenane == None: #yml action
                    try:
                        filename = fileprefix + str(sigmafile.name)
                        filename = filename.replace('.yml','_' + str(inc_filenane) + filename_ext)
                        inc_filenane += 1
                        out = open(filename, "w", encoding='utf-8')
                    except (IOError, OSError) as e:
                        print("Failed to open output file '%s': %s" % (filename, str(e)), file=sys.stderr)
                        exit(ERR_OUTPUT)
                elif  not fileprefix == None and inc_filenane == None: # a simple yml
                    try:
                        filename = fileprefix + str(sigmafile.name)
                        filename = filename.replace('.yml',filename_ext) 
                        out = open(filename, "w", encoding='utf-8')
                    except (IOError, OSError) as e:
                        print("Failed to open output file '%s': %s" % (filename, str(e)), file=sys.stderr)
                        exit(ERR_OUTPUT)
                if not cmdargs.output_fields:
                    print(result, file=out, end=newline_separator)

            if cmdargs.output_fields: # Handle output fields
                output={}
                f.seek(0)
                docs = yaml.load_all(f, Loader=yaml.FullLoader)
                for doc in docs:
                    for k,v in doc.items():
                        if k in output_fields_filtered:
                            output[k] = v
                output['rule'] = [result for result in results]
                if "filename" in output_fields_filtered:
                    output['filename'] = str(sigmafile.name)
                output_array.append(output)

            if nb_result == 0: # backend get only 1 output
                if not fileprefix == None: # want a prefix anyway
                    try:
                        filename = "%s%s_mono_output%s" % (fileprefix,cmdargs.target,filename_ext)
                        out = open(filename, "w", encoding='utf-8')
                        fileprefix = None  # no need to open the same file many time
                    except (IOError, OSError) as e:
                        print("Failed to open output file '%s': %s" % (filename, str(e)), file=sys.stderr)
                        exit(ERR_OUTPUT) 

        except OSError as e:
            print("Failed to open Sigma file %s: %s" % (sigmafile, str(e)), file=sys.stderr)
            logger.debug("* Convertion Sigma input %s FAILURE" % (sigmafile))
            success = False
            error = ERR_OPEN_SIGMA_RULE
        except (yaml.parser.ParserError, yaml.scanner.ScannerError) as e:
            print("Error: Sigma file %s is no valid YAML: %s" % (sigmafile, str(e)), file=sys.stderr)
            logger.debug("* Convertion Sigma input %s FAILURE" % (sigmafile))
            success = False
            error = ERR_INVALID_YAML
            if not cmdargs.defer_abort:
                sys.exit(error)
        except (SigmaParseError, SigmaCollectionParseError) as e:
            print("Error: Sigma parse error in %s: %s" % (sigmafile, str(e)), file=sys.stderr)
            logger.debug("* Convertion Sigma input %s FAILURE" % (sigmafile))
            success = False
            error = ERR_SIGMA_PARSING
            if not cmdargs.defer_abort:
                sys.exit(error)
        except NotSupportedError as e:
            print("Error: The Sigma rule requires a feature that is not supported by the target system: " + str(e), file=sys.stderr)
            logger.debug("* Convertion Sigma input %s FAILURE" % (sigmafile))
            success = False
            if not cmdargs.ignore_backend_errors:
                error = ERR_NOT_SUPPORTED
                if not cmdargs.defer_abort:
                    sys.exit(error)
        except BackendError as e:
            print("Error: Backend error in %s: %s" % (sigmafile, str(e)), file=sys.stderr)
            logger.debug("* Convertion Sigma input %s FAILURE" % (sigmafile))
            success = False
            if not cmdargs.ignore_backend_errors:
                error = ERR_BACKEND
                if not cmdargs.defer_abort:
                    sys.exit(error)
        except (NotImplementedError, TypeError) as e:
            print("An unsupported feature is required for this Sigma rule (%s): " % (sigmafile) + str(e), file=sys.stderr)
            # traceback.print_exc()
            logger.debug("* Convertion Sigma input %s FAILURE" % (sigmafile))
            success = False
            if not cmdargs.ignore_backend_errors:
                error = ERR_NOT_IMPLEMENTED
                if not cmdargs.defer_abort:
                    sys.exit(error)
        except PartialMatchError as e:
            print("Error: Partial field match error: %s" % str(e), file=sys.stderr)
            logger.debug("* Convertion Sigma input %s FAILURE" % (sigmafile))
            success = False
            if not cmdargs.ignore_backend_errors:
                error = ERR_PARTIAL_FIELD_MATCH
                if not cmdargs.defer_abort:
                    sys.exit(error)
        except FullMatchError as e:
            print("Error: Full field match error", file=sys.stderr)
            logger.debug("* Convertion Sigma input %s FAILURE" % (sigmafile))
            success = False
            if not cmdargs.ignore_backend_errors:
                error = ERR_FULL_FIELD_MATCH
                if not cmdargs.defer_abort:
                    sys.exit(error)                
        finally:
            try:
                f.close()
            except:
                pass
        
        if success :
            logger.debug("* Convertion Sigma input %s SUCCESS" % (sigmafile)) 
 
    result = backend.finalize()
    if result:
        print(result, file=out)

    if cmdargs.output_fields:
        if cmdargs.output_format == 'json':
            print(json.dumps(output_array, indent=4, ensure_ascii=False), file=out)
        elif cmdargs.output_format == 'yaml':
            print(ruamel.yaml.round_trip_dump(output_array), file=out)

    out.close()

    sys.exit(error)

if __name__ == "__main__":
    main()
