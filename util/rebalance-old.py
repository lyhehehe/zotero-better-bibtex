#!/usr/bin/env python3

import os
import sys
import json
from munch import Munch
import re
from ortools.algorithms import pywrapknapsack_solver

ref, output = sys.argv[1:]
if not ref.startswith('refs/heads/'):
  print(ref, 'is not a branch')
  sys.exit(0)
branch = ref.split('/')[-1]

print('rebalance', branch, '=>', output)

for job in [1, 2]:
  job = f'logs/behave-zotero-{job}-{branch}.json'
  if not os.path.exists(job):
    print('not found:', job)
    sys.exit(0)

class RunningAverage():
  def __init__(self, average=None, n=0):
    self.average = average
    self.n = n

  def add(self, new_value):
    self.n += 1
    if self.n == 1:
      self.average = new_value
    else:
      # https://math.stackexchange.com/questions/106700/incremental-averageing
      self.average = self.average + ((new_value - self.average) / self.n)

  def __float__(self):
    return self.average

  def __repr__(self):
    return "average: " + str(self.average)

class NoTestError(Exception):
  pass
class FailedError(Exception):
  pass

class Log:
  def __init__(self):
    self.tests = []

  def load(self, timings):
    tests = {}

    for feature in timings:
      if not 'elements' in feature: continue

      for test in feature.elements:
        if test.type == 'background': continue

        if test.status == 'failed':
          status = test.status
        elif not 'use.with_slow=true' in test.tags and not 'slow' in test.tags:
          status = 'fast'
        else:
          status = 'slow'

        # for retries, the last successful iteration (if any) will overwrite the failed iterations
        tests[re.sub(r' -- @[0-9]+\.[0-9]+ ', '', test.name)] = Munch(
          # convert to msecs here or too much gets rounded down to 0
          msecs=sum([step.result.duration * 1000 for step in test.steps if 'result' in step and 'duration' in step.result]), # msecs
          status=status
        )
    if len(tests) == 0: raise NoTestError()
    if any(1 for test in tests.values() if test.status == 'failed'): raise FailedError()

    for name, test in tests.items():
      self.tests.append(Munch(name=name, msecs=test.msecs, status=status))

log = Log()
try:
  for job in [1, 2]:
    with open(f'logs/behave-zotero-{job}-{branch}.json') as f:
      log.load(json.load(f, object_hook=Munch.fromDict))
  print(len(log.tests), 'tests')

  with open(output) as f:
    history = json.load(f, object_hook=Munch.fromDict)
  for name, h in list(history.duration.items()):
    if type(h) in (float, int):
      history.duration[name] = Munch(msecs=h, runs=0)
    history.duration[name].runs += history.runs

  balance = Munch.fromDict({
    'duration': {},
    'runs': history.runs + 1,
  })

  for test in log.tests:
    if h:= history.duration.get(test.name):
      avg = RunningAverage(h.msecs, h.runs)
    else:
      avg = RunningAverage()
    avg.add(test.msecs)
    balance.duration[test.name] = Munch(msecs=round(float(avg) / 10) * 10, runs=avg.n)

  for status in ['slow', 'fast']:
    tests = [test for test in log.tests if status in [ 'slow', test.status] ]
    durations = [balance.duration[test.name].msecs for test in tests]

    #if status == 'slow':
    #  solver = pywrapknapsack_solver.KnapsackSolver.KNAPSACK_MULTIDIMENSION_BRANCH_AND_BOUND_SOLVER
    #else:
    #  solver = pywrapknapsack_solver.KnapsackSolver.KNAPSACK_MULTIDIMENSION_CBC_MIP_SOLVER
    solver = pywrapknapsack_solver.KnapsackSolver.KNAPSACK_MULTIDIMENSION_CBC_MIP_SOLVER
    solver = pywrapknapsack_solver.KnapsackSolver(solver, 'TestBalancer')
    solver.Init([1 for n in durations], [durations], [int(sum(durations)/2)])
    solver.Solve()

    balance[status] = {}
    for _bin in [1, 2]:
      balance[status][_bin] = [ test.name for i, test in enumerate(tests) if solver.BestSolutionContains(i) == (_bin == 1) ]
    print(status, len(tests), 'tests,', { k: len(t) for k, t in balance[status].items()})

  # simplify for cleaner diffs
  for name, duration in list(balance.duration.items()):
    balance.duration[name].runs -= balance.runs
    if balance.duration[name].runs == 0:
      balance.duration[name] = balance.duration[name].msecs

except FileNotFoundError:
  print('logs incomplete')
  sys.exit()
except NoTestError:
  print('missing tests')
  sys.exit()
except FailedError:
  print('some tests failed')
  sys.exit()

print('writing', output)
with open(output, 'w') as f:
  json.dump(balance, f, indent='  ', sort_keys=True)
with open(os.environ['GITHUB_ENV'], 'a') as f:
  print(f"balance={json.dumps(output)}")
  print(f"balance={json.dumps(output)}", file=f)
