"""
This program facilitates the creation of a regression test case as used by the
test module.  It uses the current readability algorithm to capture a benchmark
and construct a new test case.
"""
import argparse
import errno
import os
import os.path
import readability
import sys
import test
import urllib2
import yaml

OVERWRITE_QUESTION = '%s exists; overwrite and continue (y/n)? '

def y_or_n(question):
    while True:
        response = raw_input(question).strip()
        if len(response) > 0:
            return response[0] in ['y', 'Y']

def write_file(test_name, suffix, data):
    path = os.path.join(test.TEST_DATA_PATH, test_name + suffix)
    mode = 0644
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except OSError as e:
        if e.errno == errno.EEXIST:
            if y_or_n(OVERWRITE_QUESTION % path):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            else:
                return False
        else:
            raise e
    f = os.fdopen(fd, 'w')
    f.write(data)
    return True

def write_original(test_name, url):
    orig = urllib2.urlopen(url).read()
    return write_file(test_name, test.ORIGINAL_SUFFIX, orig)

def write_readable(test_name, orig):
    rdbl_doc = readability.Document(orig)
    summary = rdbl_doc.summary()
    return write_file(test_name, test.READABLE_SUFFIX, summary.html)

def read_spec(test_name):
    yaml_path = os.path.join(
            test.TEST_DATA_PATH,
            test_name + test.YAML_EXTENSION
            )
    return test.read_yaml(yaml_path)

def read_orig(test_name, url = None):
    """
    Reads the original HTML for a given test.  If a url is provided, the HTML
    is fetched from it.  Otherwise, we look for an existing local copy.  This
    returns a pair: (HTML string, True iff the HTML has been or is already
    stored in a local copy).
    """
    if url:
        orig = urllib2.urlopen(url).read()
        write_result = write_file(test_name, test.ORIGINAL_SUFFIX, orig)
        return orig, write_result
    else:
        orig_path = os.path.join(
                test.TEST_DATA_PATH,
                test_name + test.ORIGINAL_SUFFIX
                )
        orig = open(orig_path).read()
        return orig, True

def create(args):
    spec_dict = {'url': args.url, 'test_description': args.test_description}
    spec = yaml.dump(spec_dict, default_flow_style = False)
    if not write_file(args.test_name, test.YAML_EXTENSION, spec):
        return False
    if not write_original(args.test_name, args.url):
        return False
    if not write_readable(args.test_name, args.url):
        return False
    return True

def genbench(args):
    if args.refetch:
        spec_dict = read_spec(args.test_name)
        url = spec_dict['url']
    else:
        url = None
    orig, success = read_orig(args.test_name, url)
    if not success:
        return False
    rdbl_doc = readability.Document(orig)
    summary = rdbl_doc.summary()
    if not write_file(args.test_name, test.READABLE_SUFFIX, summary.html):
        return False
    return True

DESCRIPTION = 'Create a readability regression test case.'

def main():
    parser = argparse.ArgumentParser(description = DESCRIPTION)
    subparsers = parser.add_subparsers(help = 'available subcommands')

    parser_create = subparsers.add_parser(
            'create',
            help = 'create an entirely new test'
            )
    parser_create.add_argument(
            'url',
            metavar = 'url',
            help = 'the url for which to generate a test'
            )
    parser_create.add_argument(
            'test_name',
            metavar = 'test-name',
            help = 'the name of the test'
            )
    parser_create.add_argument(
            'test_description',
            metavar = 'test-description',
            help = 'the description of the test'
            )
    parser_create.set_defaults(func = create)

    parser_genbench = subparsers.add_parser(
            'genbench',
            help = 'regenerate the benchmark for an existing test'
            )
    parser_genbench.add_argument(
            'test_name',
            metavar = 'test-name',
            help = 'the name of the test'
            )
    parser_genbench.add_argument(
            '--refetch',
            dest = 'refetch',
            action = 'store_const',
            const = True,
            default = False,
            help = 'if set, original html is refetched from the url'
            )
    parser_genbench.set_defaults(func = genbench)

    args = parser.parse_args()
    result = args.func(args)
    if not result:
        print('test was not fully generated')

if __name__ == '__main__':
    main()
