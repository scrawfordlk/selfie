#!/usr/bin/env python3

# #################### INFO ###########################################################
# This program is written as part of Christoph Siller's bachelor thesis.
# Validator is an automated checker for error inputs of C* code.
# It contains a parser for Boolectors witness format.
# A C* file is taken as input.
# The C* code is converted into a BTOR2 model using selfie -mc.
# The BTOR2 file is then checked using BtorMC.
# The resulting Witness is parsed to get the error generating input.
# The C* file is executed on Mipster (selfie) with the calculated input.
# The resulting error is compared to the predicted error.
#
# -----------------------------------------------------------------------------
# usage: validator.py [-h] [-d] [-e BAD_EXIT_CODE] [-s SELFIE_PATH]
#                     [-m BEATOR_PATH] [-b BTORMC_PATH] [-ts SELFIE_TIMEOUT]
#                     [-tb BTORMC_TIMEOUT] [-kmax KMAX] [-mem MEMORY]
#                     in_file
#
# positional arguments:
#   in_file               input C* file
#
# optional arguments:
#   -h, --help            show this help message and exit
#   -d, --debug           debug mode (generated files are kept)
#   -e BAD_EXIT_CODE, --exitcode BAD_EXIT_CODE
#                         value for non-zero exit code bad-state
#   -s SELFIE_PATH, --selfie SELFIE_PATH
#                         path to selfie executable
#   -m BEATOR_PATH, --beator BEATOR_PATH
#                         path to beator.selfie
#   -b BTORMC_PATH, --btormc BTORMC_PATH
#                         path to btormc executable
#   -ts SELFIE_TIMEOUT, --timeout_selfie SELFIE_TIMEOUT
#                         timeout for execution of in_file on mipster (example:
#                         10s, 5m, 1h)
#   -tb BTORMC_TIMEOUT, --timeout_btormc BTORMC_TIMEOUT
#                         timeout for execution of btormc with the generated
#                         btor2 file (example: 10s, 10m, 1h)
#   -kmax KMAX            -kmax parameter for btormc
#   -mem MEMORY, --memory MEMORY
#                         memory [MB] for mipster
# -----------------------------------------------------------------------------------
#
# Exitcodes:
# 0 - Error successfully verified
# 1 - Could not verify predicted error
# 2 - File Not Found
# 3 - Parser error
# 4 - Timeout
# 5 - Internal error


# #################### IMPORT ##########################################################

from sys import exit
from re import match
from os import system, stat, popen, path
from argparse import ArgumentParser

# ##################### GLOBALS ########################################################
witness = None          # input stream
output = None           # output stream
symbols = []            # holds current line of the input file split by blanks
symbol = ""             # holds the current symbol
props = []              # holds properties of the witness
memory_constraints = [] # memory assignments encoded in the witness
frame_content = []      # holds the content of the current frame
frame_number = -1       # number of current frame

# bad states are defined in the BTOR2 file and generated by selfie
bad_states = { "b0" : "ecall invalid syscall",
               "b1" : "non-zero exit code",
               "b2" : "division by zero",
               "b3" : "remainder by zero",
               "b4" : "memory access below lower bound",
               "b5" : "memory access at or above upper bound",
               "b6" : "word-unaligned memory access",
               "b7" : "memory access below lower bound",
               "b8" : "memory access at or above upper bound",
               "b9" : "word-unaligned memory access"}


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# LIBRARY LIBRARY LIBRARY LIBRARY LIBRARY LIBRARY LIBRARY LIBRARY LIBRARY LIBRARY
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# returns the next symbol of the input file
def get_symbol():
    global symbols
    global symbol

    # read next line
    if len(symbols) == 0:
        symbols = witness.readline().strip().split(" ")
        symbols.append("\n")

    # according to EBNF, semicolons can only appear at start of line
    # ";" starts a comment -> ignore line after ;
    while symbols[0][0] == ";":
        symbols.clear()
        symbols = witness.readline().strip().split(" ")
        symbols.append("\n")

    # write next symbol in symbol variable
    symbol = symbols.pop(0)


# writes error-causing program-input to file
def generate_output(frame):
    global output

    # (0*) bit-vectors are filtered out
    frame = list(filter(lambda x: int(x, 2) != 0, frame))

    if len(frame) > 1:
        print("\033[91mWarning: Frame " + frame_number + " is invalid due to multiple input values!\033[0m")

    elif len(frame) == 1:
        frame = frame[0]  # simplification since only one element in list

        if args.debug:
            print("\033[94mValue: " + str(int(frame, 2)) + " at frame " + frame_number + "\033[0m")

        try:

            # split into bytes
            for i in range(int(len(frame)/8), 0, -1):
                if args.debug:
                    print("\033[94mByte#" + str(i) + " = " + str(frame[(i-1)*8: i*8]) + "\033[0m")
                output.write(chr(int(frame[(i-1)*8 : i*8], 2)))  # write corresponding char to file

        except OverflowError:
            print("\033[91mInput Overflow at Frame " + frame_number + "\033[0m")


# ################### PARSER FUNCTIONS ###############################################

# throws an parser error and exits the program
def parser_error(expected: str):
    global symbol

    if type(expected) == str:
        print("\033[91mParser Error: '" + expected + "' expected but '" + symbol + "' found!\033[0m")
        exit(3)
    else:
        print("\033[91mInternal error: argument is not a String!\033[0m")


# EBNF: "[" binary_string "]" binary_string
def parse_array_assignment():
    global memory_constraints

    memory_address = int(symbol.strip("[]"), 2)
    get_symbol()

    if match(r'[01]+', symbol):
        value = int(symbol, 2)
        get_symbol()
    else:
        parser_error("binary string")

    memory_constraints.append([memory_address, value])


# EBNF: binary_string
def parse_bv_assignment():
    global frame_content

    if match(r'[01]+', symbol):
        frame_content.append(symbol)
        get_symbol()
    else:
        parser_error("binary string")


# EBNF: uint ( bv_assignment | array_assignment ) [ symbol ]
def parse_assignment():
    if match(r'\[[01]+\]', symbol):
        parse_array_assignment()
    else:
        parse_bv_assignment()

    if len(symbols) == 1:
        # symbol holds optional symbol after assignment
        get_symbol()


# EBNF: { comment "\n" | assignment "\n" }
def parse_model():
    # comments are handled globally
    while symbol.isnumeric():
        get_symbol()

        parse_assignment()

        if symbol == "\n":
            get_symbol()
        else:
            parser_error("\n")


# EBNF: "#" uint "\n" model
def parse_state_part():
    if match(r'#[0-9]+', symbol):
        get_symbol()

        if symbol == "\n":
            get_symbol()

            parse_model()


# EBNF: "@" uint "\n" model
def parse_input_part():
    global frame_number
    if match(r'@[0-9]+', symbol):
        frame_number = symbol.strip('@')
        get_symbol()

        if symbol == "\n":
            get_symbol()

            parse_model()

        else:
            parser_error("\n")
    else:
        parser_error("@uint")


# EBNF: [ state_part ] input_part
def parse_frame():
    global frame_content
    frame_content = []

    parse_state_part()
    parse_input_part()

    generate_output(frame_content)


# EBNF: ( "b" | "j" ) uint
def parse_prop():
    if match(r'([bj][0-9]+\n?)', symbol):
        props.append(symbol.strip())
        get_symbol()
    else:
        parser_error("Witness Property")


# EBNF: "sat\n" { prop } "\n"
def parse_header():
    if symbol == "sat":
        get_symbol()

        if symbol == "\n":
            get_symbol()

            while symbol != "\n":
                parse_prop()

            get_symbol()

            if args.debug:
                print("\033[94mProperties: " + str(props) + "\033[0m")

        else:
            parser_error("\n")
    else:
        parser_error("sat")


# EBNF: { comment "\n" } | header { frame } "."
def parse_witness():
    # comments are handled globally

    # get initial symbol
    get_symbol()

    parse_header()

    while symbol != ".":
        parse_frame()

    if args.debug:
        print("\033[94mParsing Witness finished\033[0m")


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN MAIN
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


# ################### READ ARGS #######################################################

arguments = ArgumentParser()
arguments.add_argument("-d", "--debug", dest="debug", action="store_const", const=True, default=False,
                       help="debug mode (generated files are kept)")
arguments.add_argument("-e", "--exitcode", dest="bad_exit_code", type=int, default=1,
                       help="value for non-zero exit code bad-state")
arguments.add_argument("-s", "--selfie", dest="selfie_path", default="./selfie",
                       help="path to selfie executable")
arguments.add_argument("-m", "--beator", dest="beator_path", default="./beator",
                        help="path to beator")
arguments.add_argument("-b", "--btormc", dest="btormc_path", default="btormc",
                       help="path to btormc executable")
arguments.add_argument("-ts", "--timeout_selfie", dest="selfie_timeout", default="10s",
                       help="timeout for execution of in_file on mipster (example: 10s, 5m, 1h)")
arguments.add_argument("-tb", "--timeout_btormc", dest="btormc_timeout", default="10m",
                       help="timeout for execution of btormc with the generated btor2 file (example: 10s, 10m, 1h)")
arguments.add_argument("-kmax", dest="kmax", type=int, default=10000, help="-kmax parameter for btormc")
arguments.add_argument("-mem", "--memory", dest="memory", type=int, default="2", help="memory [MB] for mipster")
arguments.add_argument(dest="in_file", help="input C* file")

args = arguments.parse_args()


# ################### GENERATING WITNESS ##############################################
# directory for temporary files
system("mkdir -p temp")
if args.debug:
    print("\033[94mtemp directory built")

# --------- generating btor2 file -----------------------------
print("\033[93mgenerating BTOR2 file using beator...\033[0m")

if args.debug:
    system(args.beator_path + " -c " + args.in_file + " - " + str(args.bad_exit_code))
else:
    # beator output is discarded
    system(args.beator_path + " -c " + args.in_file + " - " + str(args.bad_exit_code) + " 1 > /dev/null")

btor_name = path.splitext(args.in_file)[0]
system("mv " + btor_name + ".btor2 ./temp/model.btor2")

if args.debug:
    print("\033[94mBTOR2 model written to ./temp/model.btor2\033[0m")

# --------- generating witness ----------------------------------
print("\033[93mgenerating witness using btormc...\033[0m")
system('timeout ' + args.btormc_timeout + ' ' + args.btormc_path + ' -kmax ' + str(args.kmax)
       + ' ./temp/model.btor2 > ./temp/witness.wit ; if [ $? = 124 ]; then echo "btormc timed out" ; '
         'fi >> ./temp/witness.wit')

if args.debug:
    print("\033[94mwitness written to ./temp/witness.wit\033[0m")


# ################### PARSER ##########################################################

# open witness -------------------------------
try:

    # check if an error was found
    if stat("./temp/witness.wit").st_size == 0:
        print("\033[92mNo error state found!\033[0m")
        exit(0)

    # popen returns an iterabel objects of stdout lines
    result = [line.strip("\n") for line in popen('cat ./temp/witness.wit | grep -c "btormc timed out"')]

    if result[0] == '1':
        print("\033[91mError: btormc timed out!\033[0m")
        exit(4)

    witness = open("./temp/witness.wit", "r")

    if args.debug:
        print("\033[94mWitness file opened.\033[0m")

except FileNotFoundError as e:
    print(e.strerror + ": " + args.in_file)
    exit(2)


# open output file -----------------------------
output = open("./temp/error_input.txt", "w")

if args.debug:
    print("\033[94mOutput file opened.\033[0m")


print("\033[93mparsing witness...\033[0m")
parse_witness()


if args.debug:
    print("\033[94mNumber of Frames parsed: " + frame_number + "\033[0m")

for b in props:
    print("\033[93m" + bad_states[b] + " error state found!\033[0m")

if len(memory_constraints) > 0:
    print("\033[93mMemory constraints:\033[0m")
    for x in memory_constraints:
        print("\033[93m  Value: " + str(x[1]) + " at address " + str(x[0]) + "\033[0m")

if args.debug:
    print("\033[94mError causing input written to " + output.name + "\033[0m")

witness.close()
output.close()

if args.debug:
    print("\033[94mInput and Output Stream closed.\033[0m")


# #################### EXECUTE CODE WITH CALCULATED INPUT #####################################

print("\033[93mExecuting " + args.in_file + " on Mipster with calculated input...\033[0m")

# if selfie times out, the exitcode generated by the timeout tool is 124
system('timeout ' + args.selfie_timeout + ' ' + args.selfie_path + ' -c ' + args.in_file + ' -m ' + str(args.memory)
       + ' < ./temp/error_input.txt > ./temp/selfie_out.txt ; if [ $? = 124 ]; then echo "selfie timed out" ;'
         ' fi >> ./temp/selfie_out.txt')

# check if selfie timed out --------------------------------------------------
# popen returns an iterabel objects of stdout lines
result = [line.strip("\n") for line in popen('cat ./temp/selfie_out.txt | grep -c "selfie timed out"')]

if result[0] == '1':
    print("\033[91mError: Selfie timed out!\033[0m")
    exit(4)

# ##################### SEARCH FOR EXPECTED ERROR ####################################

for b in props:
    if b == 'b0':
        print("\033[91mHow the Hell did you get this error?\n\033[0m")
        error_text = '"unknown system call"'
    elif b == 'b1':
        error_text = '"exit code ' + str(args.bad_exit_code) + '"'
    elif b == 'b2' or b == 'b3':
        error_text = '"division by zero"'
    elif b == 'b4' or b == 'b5' or b == 'b6' or b == 'b7' or b == 'b8' or b == 'b9':
        error_text = '"uncaught invalid address"'
    else:
        # this should be unreachable
        print("\033[91mInternal Error - unknown bad state!\033[0m")
        exit(5)

    if args.debug:
        print("\033[94mError text: " + error_text + "\033[0m")

    # search for error text is selfie output
    result = [line.strip("\n") for line in popen('cat temp/selfie_out.txt | grep -c ' + error_text)]

    if int(result[0]) > 0:
        print("\033[92m" + bad_states[b] + " error verified!\033[0m")
        exitcode = 0
    else:
        # if the file is reading from stdout, a wrong input is taken from "selfie_out.txt" and no timeout is triggered
        print("\033[91m" + bad_states[b] + " error could not be verified. \n"
                                           "consider memory constraints and make sure the C* file is reading from stdin!\033[0m")
        exitcode = 1


# ######################### CLEANUP ############################################
# If debug mode is on, the generated files are kept for debugging purpose, else they are removed
if not args.debug:
    print("\033[93mCleanup: removing temp directory (use debug mode to keep the files)\033[0m")
    system("rm -r ./temp")
else:
    print("\033[94mAll generated files in temp directory!\033[0m")

# exits with 0 if error was verified, else with 1
exit(exitcode)
