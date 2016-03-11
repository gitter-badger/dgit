#!/usr/bin/env python 
"""
This is the core module for manipulating the dataset metadata 
"""
import os, sys, copy, fnmatch, re, shutil 
import yaml, json, tempfile 
import webbrowser 
import subprocess, string, random, pipes
import getpass
from datetime import datetime
from hashlib import sha256
import mimetypes
import platform
import uuid, shutil 
from dateutil import parser 
from ..config import get_config
from ..plugins.common import get_plugin_mgr 
from ..helper import bcolors, clean_str, cd, compute_sha256, run, clean_name
from .detect import get_schema 

############################################################
# Add files and links...
############################################################
def add_link(f):
    update = { 
        'type': 'data',
        'generator': False,
        'uuid': str(uuid.uuid1()),
        'relativepath': f,
        'mimetypes': "",
        'content': "", 
        'sha256': "",
        'localfullpath': None,
        'localrelativepath': None
    }
    return (f, update) 

def add_file_normal(f, targetdir, generator,script, source):
    """
    Add a normal file including its source
    """
    
    basename = os.path.basename(f)
    if targetdir != ".": 
        relativepath = os.path.join(targetdir, basename)
    else: 
        relativepath = basename 

    relpath = os.path.relpath(f, os.getcwd())
    filetype = 'data'
    if script: 
        filetype = 'script'
        if generator: 
            filetype = 'generator' 
        
    update = {
        'type': filetype, 
        'generator': generator, 
        'uuid': str(uuid.uuid1()),
        'relativepath': relativepath, 
        'mimetypes': mimetypes.guess_type(f)[0],
        'content': '', 
        'source': source, 
        'sha256': compute_sha256(f),
        'localfullpath': f,
        'localrelativepath': relpath, 
    }

    return (basename, update)

    
def add_files(args, targetdir, generator, source, script):
        
    # get the directory 
    ts = datetime.now().isoformat()     
    
    seen = []
    files = []
    for f in args: 
        print("Looking at", f)
        if "://" not in f:            
            (base, update) = add_file_normal(f=f,
                                             targetdir=targetdir, 
                                             generator=generator,
                                             script=script,
                                             source=source)
        else: 
            print("Adding special file")
            (base, update) = add_link(f)

        if base not in seen: 
            update['change'] = 'add'
            seen.append(base)
        else: 
            update['change'] = 'update'

        update['ts'] = ts 
        files.append(update)             
    return files

###################################################################    
# Gather files from execution of the scripts
###################################################################    

def extract_files(filename, includes): 
    """
    Extract the files to be added based on the includes 
    """

    # Load the execution strace log 
    lines = open(filename).readlines() 

    # Extract only open files - whether for read or write. You often
    # want to capture the json/ini configuration file as well
    files = {}
    lines = [l.strip() for l in lines if 'open(' in l]
    for l in lines: 

        # Check both these formats...
        # 20826 open("/usr/lib/locale/locale-archive", O_RDONLY|O_CLOEXEC) = 3
        #[28940] access(b'/etc/ld.so.nohwcap', F_OK)      = -2 (No such file or directory)
        matchedfile = re.search('open\([b]["\'](.+?)["\']', l)
        if matchedfile is None: 
            matchedfile = re.search('open\("(.+?)\"', l)
            
        if matchedfile is None: 
            continue 

        matchedfile = matchedfile.group(1) 

        if os.path.exists(matchedfile) and os.path.isfile(matchedfile): 
            
            #print("Looking at ", matchedfile) 

            # Check what action is being performed on these 
            action = 'input' if 'O_RDONLY' in l else 'output'

            matchedfile = os.path.relpath(matchedfile, ".")
            #print("Matched file's relative path", matchedfile) 

            for i in includes: 
                if fnmatch.fnmatch(matchedfile, i):
                    if matchedfile not in files: 
                        files[matchedfile] = [action] 
                    else: 
                        if action not in files[matchedfile]: 
                            files[matchedfile].append(action) 

    # A single file may be opened and closed multiple times 

    if len(files) == 0: 
        print("No input or output files found that match pattern")
        return []

    print('We captured files that matched the pattern you specified.')
    print('Please select files to keep (press ENTER)')

    # Let the user have the final say on which files must be included.
    filenames = list(files.keys())
    filenames.sort() 
    with tempfile.NamedTemporaryFile(suffix=".tmp") as temp:
        temp.write(yaml.dump(filenames, default_flow_style=False).encode('utf-8'))
        temp.flush()
        EDITOR = os.environ.get('EDITOR','/usr/bin/vi')
        subprocess.call("%s %s" %(EDITOR,temp.name), shell=True)
        temp.seek(0)
        data = temp.read() 
        selected = yaml.load(data) 

    print("You selected", len(selected), "file(s)")
    if len(selected) == 0: 
        return []

    # Get the action corresponding to the selected files
    filenames = [f for f in filenames if f in selected] 

    # Now we know the list of files. Where should they go? 
    print('Please select target locations for the various directories we found')
    print('Please make sure you do not delete any rows or edit the keys.')
    input('(press ENTER)')
    prefixes = {} 
    for f in filenames: 
        dirname = os.path.dirname(f)
        if dirname == "":
            dirname = "."
        prefixes[dirname] = dirname 

    while True: 
        with tempfile.NamedTemporaryFile(suffix=".tmp") as temp:
            temp.write(yaml.dump(prefixes, default_flow_style=False).encode('utf-8'))
            temp.flush()
            EDITOR = os.environ.get('EDITOR','/usr/bin/vi')
            subprocess.call("%s %s" %(EDITOR,temp.name), shell=True)
            temp.seek(0)
            data = temp.read() 
            try: 
                revised = yaml.load(data) 
            except Exception as e: 
                revised = {} 

            #print(list(revised.keys()))
            #print(list(prefixes.keys()))

            if set(list(revised.keys())) == set(list(prefixes.keys())):
                prefixes = revised 
                break 
            else: 
                print("Could not process edited file. Either some rows are missing or entry has YAML syntax errors")
                input("Press ENTER to continue")

    # Add the root directory back 
    if "." in prefixes: 
        prefixes[""] = prefixes["."]

    result = []
    ts = datetime.now().isoformat()     
    for f in filenames: 
        relativepath = prefixes[os.path.dirname(f)]
        if relativepath == ".": 
            relativepath = os.path.basename(f)
        else: 
            relativepath = os.path.join(relativepath, os.path.basename(f))

        result.append({
            "relativepath": relativepath, 
            'type': 'run-output',
            'actions': files[f],
            'uuid': str(uuid.uuid1()),
            'mimetypes': mimetypes.guess_type(f)[0],
            'content': open(f).read(512), 
            'sha256': compute_sha256(f),
            'ts': ts, 
            'localrelativepath': os.path.relpath(f, "."),
            "localfullpath": os.path.abspath(f),            
        })
        
    print(json.dumps(result, indent=4))
    return result

def find_executable_commitpath(repomanager, repo, args): 

    print("Finding executable commit path", args)
    # Find the first argument that is a file and is part of a repo
    for f in args: 
        if os.path.exists(f): 

            # Get full path (to get username)
            f = os.path.realpath(f) 

            # Try getting the permalink 
            (relpath, permalink) = repomanager.permalink(repo, f)
            if permalink is not None: 
                return (relpath, permalink)
    
            # Check if this part of system bin directories 
            if os.environ['HOME'] in f: 
                return (f, None) 
    
    return (None, None) 

def run_executable(repomanager, repo, 
                   args, includes): 
    """
    Run the executable and capture the input and output...
    """

    # Get platform information
    mgr = get_plugin_mgr() 
    repomgr = mgr.get(what='instrumentation', name='platform') 
    platform_metadata = repomgr.get_metadata()

    print("Obtaining Commit Information")
    (executable, commiturl) = \
            find_executable_commitpath(repomanager, repo, args) 

    # Create a local directory 
    tmpdir = tempfile.mkdtemp()

    # Construct the strace command
    print("Running the command")
    strace_filename = os.path.join(tmpdir,'strace.out.txt')
    cmd = ["strace.py", "-f", "-o", strace_filename, 
           "-s", "1024", "-q", "--"] + args 
    
    # Run the command 
    p = subprocess.Popen(cmd,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    out, err = p.communicate()

    # Capture the stdout/stderr
    stdout = os.path.join(tmpdir, 'stdout.log.txt')
    with open(stdout, 'w') as fd: 
        fd.write(out.decode('utf-8'))

    stderr = os.path.join(tmpdir, 'stderr.log.txt')
    with open(stderr, 'w') as fd: 
        fd.write(err.decode('utf-8'))

        
    # Check the strace output 
    files = extract_files(strace_filename, includes) 

    
    # Now insert the execution metadata 
    execution_metadata = { 
        'likelyexecutable': executable,
        'commitpath': commiturl, 
        'args': args,
    }
    execution_metadata.update(platform_metadata)

    for i in range(len(files)):
        files[i]['execution_metadata'] = execution_metadata

    return files 

################################################
# Main function
################################################
def add(repo, args,
        execute, generator,targetdir, 
        includes, script, source): 
    """
    Add files to the repository
    """

    # Gather the files...
    if not execute: 
        files = add_files(args=args, 
                          targetdir=targetdir,  
                          source=source,
                          script=script,
                          generator=generator)
    else: 
        files = run_executable(repomanager, repo, 
                               args, includes)


    if files is not None and len(files) > 0: 

        # Copy the files 
        repo.manager.add_files(repo, files) 

        # Update the package.json 
        rootdir = repo.rootdir 
        with cd(rootdir): 
            datapath = "datapackage.json"
            package = json.loads(open(datapath).read()) 
            
            # Update the resources 
            for h in files: 
                found = False
                for i, r in  enumerate(package['resources']):
                    if h['relativepath'] == r['relativepath']: 
                        package['resources'][i] = h
                        found = True
                        break 
                if not found: 
                    package['resources'].append(h) 

            with open(datapath, 'w') as fd: 
                fd.write(json.dumps(package, indent=4))
        
    return 
