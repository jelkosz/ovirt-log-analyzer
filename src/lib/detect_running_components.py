import os
import re
import lzma
import json
import pytz
import numpy as np
from datetime import datetime


re_timestamp = re.compile(
        r"[0-9\-]{10}[\sT][0-9]{2}:[0-9]{2}:[0-9]{2}[\.\,0-9]*[\+\-0-9Z]*")
dt_formats = ["%Y-%m-%d %H:%M:%S,%f%z", "%Y-%m-%d %H:%M:%S%z"]


def parse_date_time(line, time_zone):
    dt = re_timestamp.search(line)
    if dt is None:
        return 0
    dt = dt.group(0)
    dt = dt.replace('T', ' ')
    dt = dt.replace('Z', '+0000')
    dt = dt.replace('.', ',')
    time_part = dt.partition(' ')[2]
    # for "2017-05-12 03:23:31,135-04" format
    if (any([sign in time_part
            and len(time_part.partition(sign)[2]) == 2
            for sign in ['+', '-']])):
        dt += '00'
    elif not any([sign in time_part for sign in ['+', '-']]):
        # if we have time without time zone
        dt += time_zone
    for dt_format in dt_formats:
        try:
            date_time = datetime.strptime(dt, dt_format)
            date_time = date_time.astimezone(pytz.utc)
            date_time = date_time.timestamp()
            break
        except ValueError:
            continue
    if date_time == '':
        return 0
    return date_time


def find_time_range(output_descriptor,
                    log_directory,
                    files,
                    tz_info):
    logs_datetimes = {}
    for log_idx, log in enumerate(files):
        full_filename = os.path.join(log_directory, log)
        if log[-4:] == '.log':
                f = open(full_filename, 'rt')
        elif log[-3:] == '.xz':
            f = lzma.open(full_filename, 'rt')
        else:
            output_descriptor.write("Unknown file extension: %s" % log)
            continue
        logs_datetimes[log] = []
        dt = 0
        while dt == 0:
            dt = parse_date_time(f.readline(), tz_info[log_idx])
        logs_datetimes[log] += [dt]
        f.seek(0, os.SEEK_END)
        file_len = f.tell()
        offset = 1
        dt = 0
        while dt == 0:
            while f.read(1) != "\n":
                offset += 1
                f.seek(file_len-offset, os.SEEK_SET)
            dt = parse_date_time(f.readline(), tz_info[log_idx])
        logs_datetimes[log] += [dt]
        if logs_datetimes[log][1] < logs_datetimes[log][0]:
            output_descriptor.write(('Warning: %s - end datetime (%s) is ' +
                                     'less than start time (%s)\n') % \
                                     (log, datetime.utcfromtimestamp(
                                        logs_datetimes[log][1]).strftime(
                                                "%d-%m-%YT%H:%M:%S,%f")[:-3],
                                      datetime.utcfromtimestamp(
                                        logs_datetimes[log][0]).strftime(
                                                "%d-%m-%YT%H:%M:%S,%f")[:-3]))
            output_descriptor.write('\n')
    for log in logs_datetimes.keys():
        if (sum([logs_datetimes[log][0] -  logs_datetimes[log2][1] < 3600
                for log2 in logs_datetimes.keys()]) == 1):
            output_descriptor.write('\nWarning: log files does not cross by ' +
                                    'date time\n\n')
    return logs_datetimes


def find_all_vm_host(output_descriptor,
                     log_directory,
                     files,
                     tz_info,
                     time_range_info):
    vm_names = {}
    host_names = {}
    # list of number of first lines for the time range to pass others
    first_lines = {}
    for log_idx, log in enumerate(files):
        if time_range_info == []:
            firstline = 0
        else:
            firstline = -1
        full_filename = os.path.join(log_directory, log)
        if log[-4:] == '.log':
                f = open(full_filename)
        elif log[-3:] == '.xz':
            f = lzma.open(full_filename, 'rt')
        else:
            output_descriptor.write("Unknown file extension: %s" % log)
            continue
        if any([f in log.lower() for f in ['libvirt', 'vdsm']]):
            cur = {}
            multiline = False
            for line_num, line in enumerate(f):
                dt = parse_date_time(line, tz_info[log_idx])
                if dt == 0:
                    continue
                if (time_range_info != []):
                    if all([dt > tr[1] for tr in time_range_info]):
                        break
                    if not any([dt >= tr[0] and dt <= tr[1]
                                for tr in time_range_info]):
                        continue
                    else:
                        if firstline == -1:
                            firstline = line_num
                vm_name = re.search(r'\<name\>(.+?)\<\/name\>', line)
                if (not multiline and vm_name is not None):
                    multiline = True
                    cur['vm_name'] = vm_name.group(1)
                    continue
                vm_id = re.search(r'\<uuid\>(.+?)\<\/uuid\>', line)
                if (multiline and vm_id is not None):
                    cur['vm_id'] = vm_id.group(1)
                    continue
                host_name = re.search(r'\<hostname\>(.+?)\<\/hostname\>', line)
                if (multiline and host_name is not None):
                    cur['host_name'] = host_name.group(1)
                    continue
                host_id = re.search(r'\<hostuuid\>(.+?)\<\/hostuuid\>', line)
                if (multiline and host_id is not None):
                    cur['host_id'] = host_id.group(1)
                    if (cur['vm_name'] not in vm_names.keys()):
                        vm_names[cur['vm_name']] = set()
                    if ('<id_not_found>' in vm_names[cur['vm_name']]):
                        vm_names[cur['vm_name']].remove('<id_not_found>')
                    vm_names[cur['vm_name']].add(cur['vm_id'])
                    if (cur['host_name'] not in host_names.keys()):
                        host_names[cur['host_name']] = set()
                    if ('<id_not_found>' in host_names[cur['host_name']]):
                        host_names[cur['host_name']].remove('<id_not_found>')
                    host_names[cur['host_name']].add(cur['host_id'])
                    multiline = False
                    cur = {}
                    continue
                if multiline:
                    if (cur != {} and 'vm_name' in cur.keys() and
                            'vm_id' in cur.keys()):
                        if (cur['vm_name'] not in vm_names.keys()):
                            vm_names[cur['vm_name']] = set()
                        if ('<id_not_found>' in vm_names[cur['vm_name']]):
                            vm_names[cur['vm_name']].remove('<id_not_found>')
                        vm_names[cur['vm_name']].add(cur['vm_id'])
                    multiline = False
                    cur = {}
                    continue
                # Other types
                other_vm = re.search(r'\(VM\: name=(.+?), uuid=(.+?)\)', line)
                if other_vm is None:
                    other_vm = re.search(r'vm=(.+?), uuid=(.+?)\,', line)
                if other_vm is not None:
                    if (other_vm.group(1) not in vm_names.keys()):
                        vm_names[other_vm.group(1)] = set()
                    if ('<id_not_found>' in vm_names[other_vm.group(1)]):
                        vm_names[other_vm.group(1)].remove('<id_not_found>')
                    vm_names[other_vm.group(1)].add(other_vm.group(2))
                    continue
                if 'vdsm' in log.lower():
                    vdsm_vm = re.search(
                        r'vmId=\'(.+?)\'.+\'vmName\':\ *[u]*\'(.+?)\'', line)
                    if vdsm_vm is not None:
                        if (vdsm_vm.group(2) not in vm_names.keys()):
                            vm_names[vdsm_vm.group(2)] = set()
                        if ('<id_not_found>' in vm_names[vdsm_vm.group(2)]):
                            vm_names[vdsm_vm.group(2)].remove('<id_not_found>')
                        vm_names[vdsm_vm.group(2)].add(vdsm_vm.group(1))
                        continue
                    vdsm_host = re.search(r'I am the actual vdsm ' +
                                          r'([^\ ]+)\ +([^\ ]+)', line)
                    if vdsm_host is None:
                        continue
                    if (vdsm_host.group(2) not in host_names.keys()):
                        host_names[vdsm_host.group(2)] = set(
                                                            ['<id_not_found>'])
                    else:
                        if (len(host_names[vdsm_host.group(2)]) == 0):
                            host_names[vdsm_host.group(2)].add(
                                                            '<id_not_found>')
            first_lines[log] = firstline
            f.close()
            continue
        for line_num, line in enumerate(f):
            dt = parse_date_time(line, tz_info[log_idx])
            if dt == 0:
                continue
            if (time_range_info != []):
                if all([dt > tr[1] for tr in time_range_info]):
                    break
                if not any([dt >= tr[0] and dt <= tr[1]
                            for tr in time_range_info]):
                    continue
                elif firstline == -1:
                    firstline = line_num
            if any([v in line.lower() for v in ['vmid', 'vmname', 'vm_name']]):
                if (re.search(r"vmId=\'(.+?)\'", line) is not None):
                    res_id = re.search(r"vmId=\'(.+?)\'", line).group(1)
                elif (re.search(r"\'vmId\'\ *[:=]\ *u*\'(.+?)\'",
                                line) is not None):
                    res_id = re.search(
                        r"\'vmId\'\ *[:=]\ *u*\'(.+?)\'", line).group(1)
                else:
                    res_id = '<id_not_found>'

                if (re.search(r"vmName\ *=\ *(.+?),", line) is not None):
                    res_name = re.sub('[\'\"]', '',
                                      re.search(r"vmName\ *=\ *(.+?),",
                                                line).group(1))
                elif (re.search(r"vm\ *=\ *\'VM\ *\[([^\[\]]*?)\]\'",
                                line) is not None):
                    res_name = re.sub('[\'\"]', '',
                                      re.search(r"vm\ *=\ *\'VM\ *" +
                                                r"\[([^\[\]]*?)\]\'",
                                                line).group(1))
                elif (re.search(r"\[(.+?)=VM_NAME\]", line) is not None):
                    res_name = re.sub('[\'\"]', '',
                                      re.search(r"\[([^\[\]]*?)=VM_NAME\]",
                                                line).group(1))
                elif (re.search(r"\[(.+?)=VM\]", line) is not None):
                    res_name = re.sub('[\'\"]', '',
                                      re.search(r"\[([^\[\]]*?)=VM\]",
                                                line).group(1))
                elif (re.search(r"\'vmName\'\ *[:=]\ *u*\'([^\']*?)\'",
                                line) is not None):
                    res_name = re.sub('[\'\"]', '',
                                      re.search(r"\'vmName\'" +
                                                r"\ *[:=]\ *u*\'([^\']*?)\'",
                                                line).group(1))
                else:
                    res_name = '<name_not_found>'
                if res_name.lower() == 'null':
                    res_name = '<name_not_found>'
                if res_id.lower() == 'null':
                    res_id = '<id_not_found>'
                if ((res_name == '<name_not_found>' or res_name == '') and
                        (res_id == '<id_not_found>' or res_id == '')):
                    continue
                if res_name not in vm_names.keys():
                    vm_names[res_name] = set()
                if res_id not in vm_names[res_name]:
                    if (len(vm_names[res_name]) > 0
                            and res_id == '<id_not_found>'):
                        pass
                    elif res_id != '<id_not_found>' and \
                            '<id_not_found>' in vm_names[res_name]:
                        vm_names[res_name].remove('<id_not_found>')
                        vm_names[res_name].add(res_id)
                    else:
                        vm_names[res_name].add(res_id)
            if 'hostname' in line.lower():
                res_name = re.search(r"HostName\ *=\ *(.+?),", line)
                if res_name is not None:
                    res_name = res_name.group(1)
                else:
                    res_name = '<name_not_found>'
                res_id = re.search(r"hostId=\'(.+?)\'", line)
                if res_id is not None:
                    res_id = res_id.group(1)
                else:
                    res_id = '<id_not_found>'
                if (res_name == '<name_not_found>' and
                        (res_id == '<id_not_found>'
                         or res_id.lower() == 'null')):
                    continue
                if res_name not in host_names.keys():
                    host_names[res_name] = set()
                if res_id not in host_names[res_name]:
                    if (len(host_names[res_name]) > 0
                            and res_id == '<id_not_found>'):
                        pass
                    elif (res_id != '<id_not_found>'
                            and '<id_not_found>' in host_names[res_name]):
                        host_names[res_name].remove('<id_not_found>')
                        host_names[res_name].add(res_id)
                    else:
                        host_names[res_name].add(res_id)
            other_vm = re.search(r'VM\ *\'(.{30,40}?)\'\ *' +
                                 r'\(([^\(\)]*?)\)', line)
            if (other_vm is not None and other_vm.group(1).lower() != 'null'):
                if other_vm.group(2) not in vm_names.keys():
                    vm_names[other_vm.group(2)] = set()
                if (other_vm.group(1) not in vm_names[other_vm.group(2)]):
                    if (len(vm_names[other_vm.group(2)]) > 0
                            and other_vm.group(1) == '<id_not_found>'):
                        pass
                    elif (other_vm.group(1) != '<id_not_found>'
                            and '<id_not_found>'in
                            vm_names[other_vm.group(2)]):
                        vm_names[other_vm.group(2)].remove('<id_not_found>')
                        vm_names[other_vm.group(2)].add(other_vm.group(1))
                    else:
                        vm_names[other_vm.group(2)].add(other_vm.group(1))
        first_lines[log] = firstline
        f.close()
    if '<name_not_found>' in vm_names.keys():
        for name in vm_names.keys():
            if name == '<name_not_found>':
                continue
            for uuid in vm_names[name]:
                if uuid in vm_names['<name_not_found>']:
                    vm_names['<name_not_found>'].remove(uuid)
        if vm_names['<name_not_found>'] == set():
            vm_names.pop('<name_not_found>', None)
    return vm_names, host_names, first_lines


def find_vm_tasks_engine(first_line_number,
                         output_descriptor,
                         log_directory,
                         log,
                         file_formats,
                         tz_info,
                         time_range_info,
                         user_vms, user_hosts):
    commands_threads = {}
    long_actions = []
    if first_line_number == -1:
        # file is not in time range
        return commands_threads, long_actions
    needed_threads = set()
    if (log[-4:] == '.log'):
            f = open(log)
    elif (log[-3:] == '.xz'):
        f = lzma.open(log, 'rt')
    else:
        output_descriptor.write("Unknown file extension: %s" % log)
        return commands_threads, long_actions
    firstline = f.readline()
    for fmt in file_formats:
        prog = re.compile(fmt)
        fields = prog.search(firstline)
        if fields is not None:
            file_format = prog
            break
    if fields is None:
        # Format is not found
        return commands_threads, long_actions
    f.seek(0, 0)
    for line_num, line in enumerate(f):
        if line_num < first_line_number:
            continue
        fields = file_format.search(line)
        if fields is None:
            # Tracebacks will be added anyway
            continue
        fields = fields.groupdict()
        dt = parse_date_time(line, tz_info)
        if dt == 0:
            continue
        if (time_range_info != []):
            if all([dt > tr[1] for tr in time_range_info]):
                break
            if not any([dt >= tr[0] and dt <= tr[1]
                        for tr in time_range_info]):
                continue
        if (any([vm in fields["message"] for vm in user_vms]) or
                any([host in fields["message"] for host in user_hosts])):
            needed_threads.add(fields["thread"])
        com = re.search(r"\((.+?)\)\ +\[(.*?)\]\ +Running command:\ +" +
                        r"([^\s]+)Command", line)
        if (com is not None):
            if (com.group(1) not in commands_threads.keys()):
                commands_threads[com.group(1)] = []
            commands_threads[com.group(1)] += [
                             {'command_name': com.group(3),
                              'command_start_name': com.group(3)}]
            continue
        start = re.search(r"\((.+?)\)\ +\[(.*?)\]\ +START,\ +" +
                          r"([^\s]+)Command.*\ +log id:\ (.+)", line)
        if (start is not None):
            if (start.group(1) not in commands_threads.keys()):
                commands_threads[start.group(1)] = [
                                 {'command_name': start.group(3),
                                  'command_start_name': start.group(3),
                                  'start_time': dt,
                                  'log_id': start.group(4),
                                  'flow_id': start.group(2)}]
            else:
                com_list = [com['command_name'] for com in
                            commands_threads[start.group(1)]]
                try:
                    com_id = len(com_list) - 1 - \
                             com_list[::-1].index(start.group(3))
                    com_name = commands_threads[start.group(1)][com_id][
                                'command_name']
                    commands_threads[start.group(1)][com_id] = \
                        {'command_name': com_name,
                         'command_start_name': start.group(3),
                         'start_time': dt,
                         'log_id': start.group(4),
                         'flow_id': start.group(2)}
                except:
                    commands_threads[start.group(1)] += [
                                     {'command_name': start.group(3),
                                      'command_start_name': start.group(3),
                                      'start_time': dt,
                                      'log_id': start.group(4),
                                      'flow_id': start.group(2)}]
            continue
        finish = re.search(r"\((.+?)\)\ +\[(.*?)\]\ +FINISH,\ +" +
                           r"([^\s]+)Command.*\ +log id:\ (.+)", line)
        if (finish is not None):
            if (finish.group(1) not in commands_threads.keys()):
                commands_threads[finish.group(1)] = [
                                 {'command_name': finish.group(3),
                                  'command_start_name': finish.group(3),
                                  'log_id': finish.group(4),
                                  'flow_id': finish.group(2)}]
                # com_id = 0
                continue
            else:
                com_list = [com['command_name'] for com in
                            commands_threads[finish.group(1)]]
                try:
                    com_id = len(com_list) - 1 - \
                             com_list[::-1].index(finish.group(3))
                except:
                    commands_threads[finish.group(1)] += [{
                                     'command_name': finish.group(3),
                                     'command_start_name': finish.group(3),
                                     'log_id': finish.group(4),
                                     'flow_id': finish.group(2)}]
                    continue
            for task_idx, task in \
                    enumerate(commands_threads[finish.group(1)]):
                if ('log_id' in task.keys() and
                        task['log_id'] == finish.group(4)):
                    commands_threads[finish.group(1)][
                                            task_idx]['finish_time'] = dt
                    if ('start_time' in commands_threads[
                            finish.group(1)][task_idx].keys()):
                        commands_threads[finish.group(1)][
                                         task_idx]['duration'] = \
                            commands_threads[finish.group(1)][
                                             task_idx]['finish_time'] - \
                            commands_threads[finish.group(1)][
                                             task_idx]['start_time']
    f.close()
    # json.dump(commands_threads, open('tasks_engine_' +
    #                                  log_directory.split('/')[-2] +
    #                                  '.json',
    #                                  'w'), indent=4, sort_keys=True)
    if commands_threads != {}:
        commands_threads, long_actions = select_needed_commands(
                                            commands_threads, needed_threads)
    # json.dump(commands_threads, open('filtered_tasks_engine_' +
    #                                  log_directory.split('/')[-2] +
    #                                  '.json',
    #                                  'w'), indent=4, sort_keys=True)
    return commands_threads, long_actions


def find_vm_tasks_libvirtd(first_line_number,
                           output_descriptor,
                           log_directory,
                           log,
                           file_formats,
                           tz_info,
                           time_range_info,
                           user_vms, user_hosts):
    commands_threads = {}
    long_actions = []
    if first_line_number == -1:
        # file is not in time range
        return commands_threads, long_actions
    needed_threads = set()
    if (log[-4:] == '.log'):
            f = open(log)
    elif (log[-3:] == '.xz'):
        f = lzma.open(log, 'rt')
    else:
        output_descriptor.write("Unknown file extension: %s" % log)
        return commands_threads, long_actions
    firstline = f.readline()
    for fmt in file_formats:
        prog = re.compile(fmt)
        fields = prog.search(firstline)
        if fields is not None:
            file_format = prog
            break
    if fields is None:
        # Format is not found
        return commands_threads, long_actions
    f.seek(0, 0)
    for line_num, line in enumerate(f):
        if line_num < first_line_number:
            continue
        fields = file_format.search(line)
        if fields is None:
            # Tracebacks will be added anyway
            continue
        fields = fields.groupdict()
        dt = parse_date_time(line, tz_info)
        if dt == 0:
            continue
        if (time_range_info != []):
            if all([dt > tr[1] for tr in time_range_info]):
                break
            if not any([dt >= tr[0] and dt <= tr[1]
                        for tr in time_range_info]):
                continue
        if (any([vm in fields["message"] for vm in user_vms]) or
                any([host in fields["message"] for host in user_hosts])):
            needed_threads.add(fields["thread"])
        start = re.search(r"Thread (.+?) \((.+?)\) is now running job (.+)",
                          line)
        if (start is not None):
            if (start.group(1) not in commands_threads.keys()):
                commands_threads[start.group(1)] = []
            commands_threads[start.group(1)] += [
                             {'command_name': start.group(3),
                              'start_time': dt}]
            continue
        finish = re.search(r"Thread (.+?) \((.+?)\) finished job (.+?)" +
                           r"( .*|$)", line)
        if (finish is not None):
            if (finish.group(1) not in commands_threads.keys()):
                commands_threads[finish.group(1)] = [
                                 {'command_name': finish.group(3)}]
                continue
            else:
                com_list = [com['command_name'] for com in
                            commands_threads[finish.group(1)]]
                try:
                    com_id = len(com_list) - 1 - \
                             com_list[::-1].index(finish.group(3))
                except:
                    commands_threads[finish.group(1)] += [{
                                     'command_name': finish.group(3)}]
                    com_id = -1
            commands_threads[finish.group(1)][com_id]['finish_time'] = dt
            if ('start_time' in commands_threads[
                    finish.group(1)][com_id].keys()):
                commands_threads[finish.group(1)][com_id]['duration'] = \
                    commands_threads[finish.group(1)][
                                     com_id]['finish_time'] -\
                    commands_threads[finish.group(1)][
                                     com_id]['start_time']
    f.close()
    # json.dump(commands_threads, open('tasks_libvirtd_' +
    #                                  log_directory.split('/')[-2] +
    #                                  '.json',
    #                                  'w'), indent=4, sort_keys=True)
    if commands_threads != {}:
        commands_threads, long_actions = select_needed_commands(
                                            commands_threads, needed_threads)
    # json.dump(commands_threads, open('filtered_tasks_libvirtd_' +
    #                                  log_directory.split('/')[-2] +
    #                                  '.json',
    #                                  'w'), indent=4, sort_keys=True)
    return commands_threads, long_actions


def select_needed_commands(all_threads, needed_threads):
    vm_threads = {}
    operations_time = {}
    long_operations = {}
    for thread in all_threads:
        if thread in needed_threads:
            vm_threads[thread] = all_threads[thread]
        for command in all_threads[thread]:
            if ('duration' in command.keys()):
                if command['command_name'] not in \
                        operations_time.keys():
                    operations_time[command['command_name']] = []
                operations_time[command['command_name']] += \
                    [{'duration': command['duration'], 'start_time':
                     command['start_time']}]
    commands_ex_time = [t['duration'] for c in operations_time
                        for t in operations_time[c]]
    ex_median = np.median(commands_ex_time)
    ex_std = np.std(commands_ex_time)
    for command in sorted(operations_time.keys()):
        com_time = [c_id['duration'] for c_id in operations_time[command]]
        med_com_time = np.median(com_time)
        std_com_time = np.std(com_time)
        for c_id in operations_time[command]:
            if ((c_id['duration'] > med_com_time + 3*std_com_time) or
                    (c_id['duration'] > ex_median + 3*ex_std)):
                if command not in long_operations.keys():
                    long_operations[command] = []
                long_operations[command] += [c_id['start_time']]
    for thread in all_threads:
        for command in all_threads[thread]:
            found = False
            if 'duration' not in command.keys():
                continue
            if command['command_name'] not in long_operations.keys():
                continue
            if command['start_time'] not in long_operations[command[
                                            'command_name']]:
                continue
            if thread not in vm_threads.keys():
                vm_threads[thread] = [command]
                continue
            for existed_commands in vm_threads[thread]:
                if 'duration' not in existed_commands.keys():
                    continue
                if existed_commands['command_name'] == \
                        command['command_name'] and \
                        existed_commands['start_time'] == \
                        command['start_time']:
                    found = True
            if found:
                continue
            vm_threads[thread] += [command]
    return vm_threads, long_operations
