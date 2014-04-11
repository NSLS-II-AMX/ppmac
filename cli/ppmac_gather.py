#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
:mod:`ppmac_gather` -- Ppmac Gather Utilities
=============================================

.. module:: ppmac_gather
   :synopsis: Power PMAC gather utility functions
.. moduleauthor:: Ken Lauer <klauer@bnl.gov>
"""

from __future__ import print_function
import os
import time
import re
import sys
import ast

import matplotlib.pyplot as plt
import numpy as np

import pp_comm

servo_period = 0.442673749446657994 * 1e-3  # default
max_samples = 0x7FFFFFFF
#max_samples = 5000
gather_config_file = '/var/ftp/gather/GatherSetting.txt'
gather_output_file = '/var/ftp/gather/GatherFile.txt'


def get_sample_count(period, duration):
    return int(duration / (servo_period * period))


def get_duration(period, samples):
    return int(samples) * (servo_period * period)


def get_settings(addresses=[], period=1, duration=2.0, samples=None):
    if samples is not None:
        duration = get_duration(period, samples)
    else:
        samples = get_sample_count(period, duration)

    yield 'gather.enable=0'
    for i, addr in enumerate(addresses):
        yield 'gather.addr[%d]=%s' % (i, addr)

    yield 'gather.items=%d' % len(addresses)
    yield 'gather.Period=%d' % period
    yield 'gather.enable=1'
    yield 'gather.enable=0'
    yield 'gather.MaxSamples=%d' % samples


def read_settings_file(comm, fn=None):
    def get_index(name):
        m = re.search('\[(\d+)\]', name)
        if m:
            return int(m.groups()[0])
        return None

    def remove_indices_and_brackets(name):
        return re.sub('(\[\d+\]?)', '', name)

    if fn is None:
        fn = gather_config_file

    lines = comm.read_file(fn)
    settings = {}
    for line in lines:
        line = line.strip()
        lower_line = line.lower()
        if lower_line.startswith('gather') and '=' in lower_line:
            var, _, value = line.partition('=')
            var = var.lower()
            if '[' in var:
                base = remove_indices_and_brackets(var)
                index = get_index(var)
                if index is None:
                    settings[var] = value
                else:
                    if base not in settings:
                        settings[base] = {}
                    settings[base][index] = value
            else:
                settings[var] = value

    if 'gather.addr' in settings:
        addr_dict = settings['gather.addr']
        # addresses comes in as a dictionary of {index: value}
        max_addr = max(addr_dict.keys())
        addr = [''] * (max_addr + 1)
        for index, value in addr_dict.items():
            addr[index] = value

        settings['gather.addr'] = addr

    return settings


def parse_gather(addresses, lines):
    def fix_line(line):
        try:
            return [ast.literal_eval(num) for num in line]
        except Exception as ex:
            print('Unable to parse gather results (%s): %s' %
                  (ex.__class__.__name__, ex))
            print('->', line)
            return []

    count = len(addresses)
    data = [fix_line(line.split(' '))
            for line in lines
            if line.count(' ') == (count - 1)]

    if 'Sys.ServoCount.a' in addresses:
        idx = addresses.index('Sys.ServoCount.a')
        for line in data:
            line[idx] = line[idx] * servo_period

    return data


def gather(comm, addresses, duration=0.1, period=1, output_file=gather_output_file):
    comm.close_gpascii()

    total_samples = get_sample_count(period, duration)

    settings = get_settings(addresses, duration=duration, period=period)

    if comm.send_file(gather_config_file, '\n'.join(settings)):
        print('Wrote configuration to', gather_config_file)

    comm.shell_command('gpascii -i%s' % gather_config_file)

    max_lines = comm.get_variable('gather.maxlines', type_=int)
    if max_lines < total_samples:
        total_samples = max_lines
        duration = get_duration(period, total_samples)
        comm.set_variable('gather.maxsamples', total_samples)

        print('* Warning: Buffer not large enough.')
        print('  Maximum count with the current addresses: %d' % (max_lines, ))
        print('  New duration is: %.2f s' % (duration, ))

    comm.open_gpascii()
    comm.set_variable('gather.enable', 2)
    samples = 0

    print('Waiting for %d samples' % total_samples)
    try:
        while samples < total_samples:
            samples = comm.get_variable('gather.samples', type_=int)
            if total_samples != 0:
                percent = 100. * (float(samples) / total_samples)
                print('%-6d/%-6d (%.2f%%)' % (samples, total_samples,
                                              percent),
                      end='\r')
                sys.stdout.flush()
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        print()

    comm.set_variable('gather.enable', 0)
    comm.close_gpascii()
    return get_gather_results(comm, addresses, output_file)


def get_gather_results(comm, addresses, output_file=gather_output_file):
    # -u is for upload
    comm.shell_command('gather %s -u' % (output_file, ))
    return parse_gather(addresses,
                        comm.read_file(output_file, timeout=60.0))


def gather_data_to_file(fn, addr, data, delim='\t'):
    with open(fn, 'wt') as f:
        print(delim.join(addr), file=f)
        for line in data:
            line = ['%s' % s for s in line]
            print(delim.join(line), file=f)


def gather_to_file(comm, addr, fn, delim='\t', **kwargs):
    data = gather_to_file(comm, addr, **kwargs)
    return gather_data_to_file(fn, addr, data, delim=delim)


def plot(addr, data):
    x_idx = addr.index('Sys.ServoCount.a')

    data = np.array(data)
    x_axis = data[:, x_idx] - data[0, x_idx]
    for i in range(len(addr)):
        if i == x_idx:
            pass
        else:
            plt.figure(i)
            plt.plot(x_axis, data[:, i], label=addr[i])
            plt.legend()

    print('Plotting...')
    plt.show()


def gather_and_plot(comm, addr, duration=0.2, period=1):
    servo_period = comm.get_variable('Sys.ServoPeriod', type_=float) * 1e-3
    print('Servo period is', servo_period)

    data = gather(comm, addr, duration=duration, period=period)
    gather_data_to_file('test.txt', addr, data)
    plot(addr, data)


def other_trajectory(move_type, motor, distance, velocity=1, accel=1, dwell=0, reps=1, one_direction=False, kill=True):
    """
    root@10.0.0.98:/opt/ppmac/tune# ./othertrajectory
    You need 9 Arguments for this function
            Move type (1:Ramp ; 2: Trapezoidal 3:S-Curve Velocity
            Motor Number
            Move Distance(cts)
            Velocity cts/ms
            SAcceleration time (cts/ms^2)
            Dwell after move time (ms)
            Number of repetitions
            Move direction flag (0:move in both direction 1: move in only one direction)  in
            Kill flag (0 or 1)
    Please try again.
    """
    print('other trajectory', motor, move_type)
    assert(move_type in (OT_RAMP, OT_TRAPEZOID, OT_S_CURVE))
    velocity = abs(velocity)

    print(locals().keys())
    args = ['%(move_type)d',
            '%(motor)d',
            '%(distance)f',
            '%(velocity)f',
            '%(accel)f',
            '%(dwell)d',
            '%(reps)d',
            '%(one_direction)d',
            '%(kill)d',
            ]

    args = ' '.join([arg % locals() for arg in args])
    return '%s %s' % (tune_paths['othertrajectory'], args)


def plot_tune_results(columns, data,
                      keys=['Sys.ServoCount.a',
                            'Desired', 'Actual',
                            'Velocity']):

    data = np.array(data)
    idx = [columns.index(key) for key in keys]
    x_axis, desired, actual, velocity = [data[:, i] for i in idx]

    fig, ax1 = plt.subplots()
    ax1.plot(x_axis, desired, color='black', label='Desired')
    ax1.plot(x_axis, actual, color='b', label='Actual')
    ax1.set_xlabel('Time (s)')
    ax1.set_ylabel('Position (motor units)')
    for tl in ax1.get_yticklabels():
        tl.set_color('b')

    error = desired - actual
    ax2 = ax1.twinx()
    ax2.plot(x_axis, error, color='r', alpha=0.4, label='Following error')
    ax2.set_ylabel('Error (motor units)')
    for tl in ax2.get_yticklabels():
        tl.set_color('r')

    plt.xlim(min(x_axis), max(x_axis))
    plt.show()


def run_tune_program(comm, cmd, result_path='/var/ftp/gather/othertrajectory_gather.txt'):
    print('Running tune', cmd)
    comm.shell_command(cmd)
    lines, groups = comm.wait_for('^(.*)\s+finished Successfully!$', verbose=True, timeout=50)
    print('Tune finished (%s)' % groups[0])

    columns = ['Sys.ServoCount.a',
               'Desired',
               'Actual',
               'Velocity']

    data = get_gather_results(comm, columns, result_path)
    plot_tune_results(columns, data)

BIN_PATH = '/opt/ppmac'
TUNE_PATH = os.path.join(BIN_PATH, 'tune')
TUNE_TOOLS = ('analyzerautotunemove', 'autotunecalc',
              'autotunemove', 'chirpmove',
              'currentautotunecalc', 'currentstep',
              'filtercalculation', 'openloopchirp',
              'openloopsine', 'openlooptestmove',
              'othertrajectory', 'parabolicmove',
              'randommove', 'sinesweep',
              'sinusoidal', 'stepmove', 'usertrajectory')
tune_paths = dict((tool, os.path.join(TUNE_PATH, tool))
                  for tool in TUNE_TOOLS)
OT_RAMP = 1
OT_TRAPEZOID = 2
OT_S_CURVE = 3
import functools


def _other_traj(move_type):
    @functools.wraps(other_trajectory)
    def wrapped(*args, **kwargs):
        return other_trajectory(move_type, *args, **kwargs)
    return wrapped

ramp = _other_traj(OT_RAMP)
trapezoid = _other_traj(OT_TRAPEZOID)
s_curve = _other_traj(OT_S_CURVE)


def geterrors_motor(motor, time_=0.3, abort_cmd='', m_mask=0x7ac, c_mask=0x7ac, r_mask=0x1e, g_mask=0xffffffff):
    exe = '/opt/ppmac/geterrors/geterrors'
    args = '-t %(time_).1f -#%(motor)d -m0x%(m_mask)x -c0x%(c_mask)x -r0x%(r_mask)x -g0x%(g_mask)x' % locals()
    if abort_cmd:
        args += ' -S"%(abort_cmd)s"'

    print(exe, args)


def run_and_gather(comm, script_text, prog=999, coord_sys=0,
                   gather_vars=[], period=1, samples=max_samples,
                   cancel_callback=None, check_active=False):
    """
    Run a motion program and read back the gathered data
    """

    if 'gather.enable' not in script_text.lower():
        script_text = '\n'.join(['gather.enable=2',
                                 script_text,
                                 'gather.enable=0'
                                 ])

    comm.set_variable('gather.enable', '0')

    gather_lower = [var.lower() for var in gather_vars]

    if 'sys.servocount.a' not in gather_lower:
        gather_vars = list(gather_vars)
        gather_vars.insert(0, 'Sys.ServoCount.a')

    settings = get_settings(gather_vars, period=period,
                            samples=samples)
    if comm.send_file(gather_config_file, '\n'.join(settings)):
        print('Wrote configuration to', gather_config_file)

    comm.shell_command('gpascii -i%s' % gather_config_file)

    comm.open_gpascii()

    for line in script_text.split('\n'):
        comm.send_line(line)

    comm.program(coord_sys, prog, start=True)

    if check_active:
        active_var = 'Coord[%d].ProgActive' % prog
    else:
        active_var = 'gather.enable'

    def get_status():
        return comm.get_variable(active_var, type_=int)

    try:
        #time.sleep(1.0 + abs((iterations * distance) / velocity))
        print("Waiting...")
        while get_status() == 0:
            time.sleep(0.1)

        while get_status() != 0:
            samples = comm.get_variable('gather.samples', type_=int)
            print("Working... got %6d data points" % samples, end='\r')
            time.sleep(0.1)

        print()
        print('Done')

    except KeyboardInterrupt as ex:
        print()
        print('Cancelled - stopping program')
        comm.program(coord_sys, prog, stop=True)
        if cancel_callback is not None:
            cancel_callback(ex)

    try:
        for line in comm.read_timeout(timeout=0.1):
            if 'error' in line:
                print(line)
    except pp_comm.TimeoutError:
        pass

    data = get_gather_results(comm, gather_vars, gather_output_file)
    return gather_vars, data


def main():
    global servo_period

    addr = ['Sys.ServoCount.a',
            'Motor[3].Pos.a',
            #'Motor[4].Pos.a',
            #'Motor[5].Pos.a',
            ]
    duration = 10.0
    period = 1

    from pp_comm import PPComm

    comm = PPComm()
    comm.open_channel()
    servo_period = comm.servo_period
    print('new servo period is', servo_period)

    ramp_cmd = ramp(3, distance=0.01, velocity=0.01)
    if 1:
        run_tune_program(comm, ramp_cmd)
    else:
        gather_and_plot(comm, addr, duration=duration, period=period)


if __name__ == '__main__':
    main()
