import os
import re
import lzma
import progressbar
from multiprocessing import Manager, Pool
from lib.create_error_definition import loop_over_lines
from lib.errors_statistics import merge_all_errors_by_time, \
                                  clusterize_messages, \
                                  calculate_events_frequency
from lib.represent_statistics import print_only_dt_message
from lib.detect_running_components import find_vm_tasks_engine, \
                                          find_vm_tasks_libvirtd, \
                                          find_all_vm_host, \
                                          find_time_range
from lib.ProgressPool import ProgressPool
from progressbar import ProgressBar


class LogAnalyzer:
    # out_descr
    # directory
    # filenames[]
    # time_zones[]
    # formats_templates[]
    # ----
    # found_logs['log1',...]
    # log_file_format{"log1":...,}
    # all_errors{"log1":...,}
    # format_fields{'log1':...}
    def __init__(self, out_descr, directory, filenames, tz,
                 time_ranges, vms, events, hosts, templates_filename):
        self.out_descr = out_descr
        self.directory = directory
        self.time_ranges = time_ranges
        self.vms = vms
        self.events = events
        self.hosts = hosts
        # parse formats file
        formats = open(templates_filename, 'r').read().split('\n')
        self.formats_templates = []
        format_num = 0
        for line in formats:
            if line[0] == '@':
                format_name = line[1:]
            elif line[0:2] == 'r^' and format_name != '':
                try:
                    re.compile(line[1:])
                except:
                    self.out_descr.write("Wrong format of regexp: %s\n" %
                                         line[1:])
                    exit()
                self.formats_templates += [{'name': format_name,
                                            'regexp': line[1:]}]
                format_name = ''
                format_num += 1
            else:
                self.out_descr.write("Wrong format of template: %s\n" % line)
        self.found_logs = []
        self.log_files_format = []
        self.time_zones = []
        for log in filenames:
            full_filename = os.path.join(self.directory, log)
            if not os.path.isfile(full_filename):
                self.out_descr.write("File not found: %s\n" % log)
                continue
            # save name of actually opened logfile
            self.found_logs += [log]
            # save log's time zome
            self.time_zones += [tz[log]]
            # find format of a log
            if log[-4:] == '.log':
                f = open(full_filename)
                line = f.readline()
                f.close()
            elif log[-3:] == '.xz':
                f = lzma.open(full_filename, 'rt')
                line = f.readline()
                f.close()
            else:
                self.out_descr.write("Unknown file extension: %s" % log)
                continue
            for file_format_num in range(len(self.formats_templates)):
                prog = re.compile(self.formats_templates[file_format_num][
                                                    'regexp'])
                result = prog.search(line)
                if result is not None:
                    self.log_files_format += [prog]
                    break
        if (self.found_logs == []):
            out_descr.write('No logfiles found.\n')
            exit()

    def find_vms_and_hosts(self):
        vm_names, host_names, self.first_lines = find_all_vm_host(
                                                    self.out_descr,
                                                    self.directory,
                                                    self.found_logs,
                                                    self.time_zones,
                                                    self.time_ranges)
        self.all_vms = vm_names
        self.all_hosts = host_names
        if self.vms == []:
            for k in vm_names.keys():
                self.vms += [k]
                for i in vm_names[k]:
                    self.vms += [i]
        if self.hosts == []:
            for k in host_names.keys():
                self.hosts += [k]
                for i in host_names[k]:
                    self.hosts += [i]

    def read_time_ranges(self):
        self.total_time_ranges = find_time_range(self.out_descr,
                                                 self.directory,
                                                 self.found_logs,
                                                 self.time_zones)

    def find_vm_tasks(self):
        engine_formats = [fmt['regexp'] for fmt in self.formats_templates if
                          'engine' in fmt['name']]
        libvirtd_formats = [fmt['regexp'] for fmt in self.formats_templates if
                            'libvirt' in fmt['name']]
        self.vm_tasks = {}
        self.long_tasks = {}
        for idx, log in enumerate(self.found_logs):
            if 'engine' in log.lower():
                tasks_file, long_tasks_file = \
                    find_vm_tasks_engine(self.first_lines[log],
                                         self.out_descr,
                                         self.directory,
                                         os.path.join(self.directory, log),
                                         engine_formats,
                                         self.time_zones[idx],
                                         self.time_ranges,
                                         self.vms,
                                         self.hosts)
                self.vm_tasks.update(tasks_file)
                self.long_tasks.update(long_tasks_file)
            elif 'libvirt' in log.lower():
                tasks_file, long_tasks_file = \
                    find_vm_tasks_libvirtd(self.first_lines[log],
                                           self.out_descr,
                                           self.directory,
                                           os.path.join(self.directory, log),
                                           libvirtd_formats,
                                           self.time_zones[idx],
                                           self.time_ranges,
                                           self.vms,
                                           self.hosts)
                self.vm_tasks.update(tasks_file)
                self.long_tasks.update(long_tasks_file)

    def load_data(self, show_warnings, show_progressbar):
        self.all_errors = {}
        self.format_fields = {}
        m = Manager()
        q = m.Queue()
        idxs = range(len(self.found_logs))
        if show_progressbar:
            result = ProgressPool([(process_files,
                                    "{}".format(self.found_logs[i]),
                                    [i, self.found_logs,
                                     self.log_files_format,
                                     self.directory,
                                     self.time_zones,
                                     self.first_lines,
                                     q,
                                     self.events,
                                     self.hosts,
                                     self.time_ranges,
                                     self.vms,
                                     # list(self.vm_tasks.keys()) +
                                     list(self.long_tasks.keys()),
                                     [mes['flow_id'] for t in
                                      self.vm_tasks.keys() for mes in
                                      (self.vm_tasks)[t] if ('flow_id'
                                      in mes.keys() and mes['flow_id'] != '')],
                                     show_warnings])
                                   for i in idxs], processes=1)
        else:
            result = []
            run_args = [[i, self.found_logs,
                         self.log_files_format,
                         self.directory,
                         self.time_zones,
                         self.first_lines,
                         q,
                         self.events,
                         self.hosts,
                         self.time_ranges,
                         self.vms,
                         # list(self.vm_tasks.keys()) +
                         list(self.long_tasks.keys()),
                         [mes['flow_id'] for t in self.vm_tasks.keys()
                          for mes in (self.vm_tasks)[t] if ('flow_id'
                          in mes.keys() and mes['flow_id'] != '')],
                         show_warnings] for i in idxs]
            widget_style = ['Load: ', progressbar.Percentage(), ' (',
                            progressbar.SimpleProgress(), ')', ' ',
                            progressbar.Bar(), ' ', progressbar.Timer(), ' ',
                            progressbar.AdaptiveETA()]
            bar = ProgressBar(widgets=widget_style)
            with Pool(processes=1) as pool:
                worker = pool.imap(star, run_args)
                for _ in bar(run_args):
                    result += [worker.next()]
        for idx, log in enumerate(self.found_logs):
            self.all_errors[log] = result[idx][0]
            # saving logfile format fields names
            self.format_fields[log] = result[idx][1]
        while not q.empty():
            warn = q.get()
            self.out_descr.write(warn)
        if (self.all_errors == {} or all([self.all_errors[l] == []
                                          for l in self.all_errors.keys()])):
            self.out_descr.write('No matches.\n')
            exit()

    def find_important_events(self):
        timeline, merged_errors, self.all_fields = \
            merge_all_errors_by_time(self.all_errors, self.format_fields)
        try:
            del self.all_errors
        except:
            pass
        # f = open('debug_'+self.directory.split('/')[-2]+'.txt', 'w')
        # for msg in merged_errors:
        #     for field in msg:
        #         f.write(str(field) + '   ')
        #     f.write('\n')
        # f.close()
        self.timeline = timeline
        clusters, merged_errors, self.all_fields, needed_messages, \
            reasons = clusterize_messages(merged_errors, self.all_fields,
                                          self.events +
                                          self.vms +
                                          self.hosts,
                                          self.directory)
        important_events, new_fields = \
            calculate_events_frequency(merged_errors,
                                       clusters,
                                       self.all_fields,
                                       timeline,
                                       self.events +
                                       self.hosts +
                                       self.vms,
                                       self.vm_tasks,
                                       self.long_tasks,
                                       self.all_vms,
                                       self.all_hosts,
                                       needed_messages,
                                       reasons)
        return important_events, new_fields

    def print_errors(self, errors_list, new_fields, out):
        # print_all_headers(errors_list, self.list_headers,
        #                   self.format_fields, out)
        print_only_dt_message(errors_list, new_fields, out)


def star(input):
    return process_files(*input)


def process_files(idx, log, formats_templates, directory, time_zones,
                  first_lines, out_descr, events, hosts, time_ranges, vms,
                  tasks, flow_ids, show_warnings, progressbar=None,
                  text_header=None):
    if text_header:
        text_header.update_mapping(type_op="Parsing:")
    # gathering all information about errors from a logfile into lists
    lines_info, fields_names = loop_over_lines(directory,
                                               log[idx],
                                               formats_templates[idx],
                                               time_zones[idx],
                                               first_lines[log[idx]],
                                               out_descr,
                                               events,
                                               hosts,
                                               time_ranges,
                                               vms,
                                               tasks,
                                               flow_ids,
                                               show_warnings,
                                               progressbar)
    return lines_info, fields_names

    # def dump_to_json(self, path, outfile,
    #                 template='chart_errors_statistics_template.html'):
    #    pass
