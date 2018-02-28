#!/usr/bin/env python

import pwd
import grp
import getpass
import socket

import subprocess
import os
import sys
import time


def get_cluster():

    name = socket.gethostname()

    host = name.split('.')
    if (len(host) < 2):
        idx = 0
    else:
        idx = 1
    return host[idx]


def get_group_members(this_group_id):

    with open('/etc/yalehpc', 'r') as f:
        f.readline()
        mgt = f.readline().split('=')[1].replace('"', '').rstrip()

    query = "LDAPTLS_REQCERT=never ldapsearch -xLLL -H ldaps://{0}  -b o=hpc.yale.edu -D".format(mgt)
    query += " cn=client,o=hpc.yale.edu -w hpc@Client 'gidNumber={0}' uid | grep '^uid'".format(this_group_id)
    result = subprocess.check_output([query], shell=True)

    group_members = result.replace('uid: ', '').split('\n')

    return group_members[:-1]


def parse_quota_line(line, usage):

    split = line.split(':')

    quota_type = split[7]
    if quota_type == 'FILESET':
        fileset = split[9]
        name = ''
    else:
        name = split[9]
        fileset = split[-2]

    data = [int(split[10])/1024/1024, int(split[11])/1024/1024,
            int(split[15]), int(split[16])]

    if usage:
        output = format_for_usage(fileset, name, data)
        return fileset, name, output

    else:
        output = format_for_summary(fileset, quota_type, data)
        return fileset, name, output


def format_for_usage(fileset, user, data):

    return '{0:14}{1:6}{2:10}{3:14,}'.format(fileset, user,
                                             data[0], data[2])


def format_for_summary(fileset, quota_type, data):

    return '{0:14}{1:8}{2:12}{3:12}{4:14,}{5:14,}'.format(fileset, quota_type,
                                                          data[0], data[1],
                                                          data[2], data[3])


def read_usage_file(filename, this_user, group_members):

    quota_data = {}
    user_filesets = set()

    with open(filename, 'r') as f:
        f.readline()
        for line in f:

            if 'USR' not in line or 'root' in line:
                continue

            fileset, user, output = parse_quota_line(line, True)

            if fileset not in quota_data.keys():
                quota_data[fileset] = {}
            quota_data[fileset][user] = output

            if user == this_user or (this_user is None and user in group_members):
                user_filesets.add(fileset)

    return quota_data, list(user_filesets)


def compile_usage_output(filesets, group_members, cluster, data):

    output = ['', '', '']

    for fileset in sorted(filesets):
        section = []

        if 'pi' in fileset:
            for user in sorted(data[fileset].keys()):
                section.append(data[fileset][user])
            output.append('\n'.join(section))

        else:
            for group_member in sorted(group_members):
                if group_member not in data[fileset].keys():
                    section.append(format_for_usage(fileset, group_member, [0, 0, 0, 0]))
                else:
                    section.append(data[fileset][group_member])

            if 'home' in fileset:
                output[0] = '\n'.join(section)
            elif 'scratch.' in fileset or 'project' in fileset:
                output[1] = '\n'.join(section)
            elif 'scratch60' in fileset:
                output[2] = '\n'.join(section)

    return '\n----\n'.join(output)


def live_quota_data(device, filesets, user, group):

    quota_script = '/usr/lpp/mmfs/bin/mmlsquota'
    output = ['', '', '']

    query = '{0} -eg {1} -Y --block-size auto {2}'.format(quota_script, group, device)
    result = subprocess.check_output([query], shell=True)

    # add user quotas for LS home directories
    if cluster in ['farnam', 'ruddle']:
        query = '{0} -eu {1} -Y --block-size auto {2} | grep home'.format(quota_script, user, device)
        result += subprocess.check_output([query], shell=True)

    for quota in result.split('\n'):
        if 'HEADER' in quota or 'root' in quota or len(quota) < 10:
            continue
        fileset, _, section = parse_quota_line(quota, False)

        if 'home' in fileset:
            output[0] = section

        elif 'scratch.' in fileset or 'project' in fileset:
            output[1] = section

        elif 'scratch60' in fileset:
            output[2] = section

    for fileset in filesets:
        if 'pi' in fileset:
            query = '{0} -ej {1} -Y --block-size auto {2}'.format(quota_script, fileset, device)
            pi_quota = subprocess.check_output([query], shell=True)
            output.append(parse_quota_line(pi_quota.split('\n')[1], False)[-1])

    return '\n'.join(output)


def cached_quota_data(filename, filesets, group):

    output = ['', '']

    with open(filename, 'r') as f:
        f.readline()
        for line in f:

            if 'USR' in line or 'root' in line:
                continue

            fileset, name, section = parse_quota_line(line, False)

            if fileset in filesets:
                if name == group:
                    if 'scratch.' in fileset or 'project' in fileset:
                        output[0] = section

                    elif 'scratch60' in fileset:
                        output[1] = section

                if 'pi' in fileset and 'FILESET' in section:
                    output.append(section)

        return '\n'.join(output)


if (__name__ == '__main__'):

    if len(sys.argv) == 3:
        if sys.argv[1] == '-u':
            user = sys.argv[2]
        elif sys.argv[1] == '-g':
            user = None
            group_id = grp.getgrnam(sys.argv[2]).gr_gid
        else:
            sys.exit("Unknown arguement. Use -u <user>, -g <group> or no argument for current user")
        is_me = False

    else:
        user = getpass.getuser()
        is_me = True

    if user is not None:
        group_id = pwd.getpwnam(user).pw_gid
    group_name = grp.getgrgid(group_id).gr_name

    cluster = get_cluster()

    filesystem = {'farnam': '/gpfs/ysm',
                  'grace': '/gpfs/loomis',
                  }
    device = {'farnam': 'ysm-gpfs'}

    # usage details
    usage_filename = filesystem[cluster] + '/.mmrepquota/current'
    timestamp = time.strftime('%b %d %Y %H:%M', time.gmtime(os.path.getmtime(usage_filename)))
    group_members = get_group_members(group_id)

    usage_data, filesets = read_usage_file(usage_filename, user, group_members)
    usage_output = compile_usage_output(filesets, group_members, cluster, usage_data)

    # quota summary
    if is_me:
        quota_output = live_quota_data(device[cluster], filesets, user, group_id)
    else:
        quota_output = cached_quota_data(usage_filename, filesets, group_name)

    header = "This script shows information about your quotas on the current gpfs filesystem.\n"
    header += "If you plan to poll this sort of information extensively, please use alternate means\n"
    header += "and/or contact us for help at hpc@yale.edu\n\n"
    header += '## Usage Details for {0} (as of {1})\n'.format(group_name, timestamp)
    header += '{0:14}{1:6}{2:10}{3:14}\n'.format('Fileset', 'User', 'Usage (GB)', '  File Count')
    header += '{0:14}{1:6}{2:10}{3:14}'.format('-'*13, '-'*5, '-'*10, ' '+'-'*13)

    print(header)
    print(usage_output)

    if is_me:
        time = 'right now'
    else:
        time = timestamp

    header = '\n## Quota Summary for {0} (as of {1})\n'.format(group_name, time)
    header += '{0:14}{1:8}{2:12}{3:12}{4:14}{5:14}\n'.format('Fileset', 'Type', ' Usage (GB)',
                                                             ' Quota (GB)', ' File Count', ' File Limit')
    header += '{0:14}{1:8}{2:12}{3:12}{4:14}{5:14}'.format('-'*13, '-'*7, ' '+'-'*11,
                                                           ' '+'-'*11, ' '+'-'*13, ' '+'-'*13)

    print(header)
    print(quota_output)
