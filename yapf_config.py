import fnmatch
import os

import gclient_utils

# File name for yapf style config files.
YAPF_CONFIG_FILENAME = '.style.yapf'


def FindYapfConfigFile(fpath, yapf_config_cache, top_dir=None):
    """Checks if a yapf file is in any parent directory of fpath until top_dir.

    Recursively checks parent directories to find yapf file and if no yapf file
    is found returns None. Uses yapf_config_cache as a cache for previously found
    configs.
    """
    fpath = os.path.abspath(fpath)
    # Return result if we've already computed it.
    if fpath in yapf_config_cache:
        return yapf_config_cache[fpath]

    parent_dir = os.path.dirname(fpath)
    if os.path.isfile(fpath):
        ret = FindYapfConfigFile(parent_dir, yapf_config_cache, top_dir)
    else:
        # Otherwise fpath is a directory
        yapf_file = os.path.join(fpath, YAPF_CONFIG_FILENAME)
        if os.path.isfile(yapf_file):
            ret = yapf_file
        elif fpath in (top_dir, parent_dir):
            # If we're at the top level directory, or if we're at root
            # there is no provided style.
            ret = None
        else:
            # Otherwise recurse on the current directory.
            ret = FindYapfConfigFile(parent_dir, yapf_config_cache, top_dir)
    yapf_config_cache[fpath] = ret
    return ret


def GetYapfIgnorePatterns(top_dir: str):
    """Returns all patterns in the .yapfignore file.

    yapf is supposed to handle the ignoring of files listed in .yapfignore itself,
    but this functionality appears to break when explicitly passing files to
    yapf for formatting. According to
    https://github.com/google/yapf/blob/HEAD/README.rst#excluding-files-from-formatting-yapfignore,
    the .yapfignore file should be in the directory that yapf is invoked from,
    which we assume to be the top level directory in this case.

    Args:
        top_dir: The top level directory for the repository being formatted.

    Returns:
        A set of all fnmatch patterns to be ignored.
    """
    yapfignore_file = os.path.join(top_dir, '.yapfignore')
    ignore_patterns = set()
    if not os.path.exists(yapfignore_file):
        return ignore_patterns

    for line in gclient_utils.FileRead(yapfignore_file).split('\n'):
        stripped_line = line.strip()
        # Comments and blank lines should be ignored.
        if stripped_line.startswith('#') or stripped_line == '':
            continue
        ignore_patterns.add(stripped_line)
    return ignore_patterns


def FilterYapfIgnoredFiles(filepaths, patterns):
    """Filters out any filepaths that match any of the given patterns.

    Args:
        filepaths: An iterable of strings containing filepaths to filter.
        patterns: An iterable of strings containing fnmatch patterns to filter on.

    Returns:
        A list of strings containing all the elements of |filepaths| that did not
        match any of the patterns in |patterns|.
    """
    # Not inlined so that tests can use the same implementation.
    return [
        f for f in filepaths
        if not any(fnmatch.fnmatch(f, p) for p in patterns)
    ]
