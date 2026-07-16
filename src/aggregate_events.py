#!/usr/bin/env python3
"""Aggregate LLM event labels into a corpus distribution (counts, role and round profiles)."""
import json, glob, collections, argparse, os

ap = argparse.ArgumentParser()
ap.add_argument("--units", default="../analysis/states_events/units.jsonl")
ap.add_argument("--labels", default="../analysis/event_labels/deepseek")
args = ap.parse_args()

units = [json.loads(l) for l in open(args.units)]
total = collections.Counter()
by_role = collections.defaultdict(collections.Counter)
by_round = collections.defaultdict(collections.Counter)
n = 0
for fp in glob.glob(os.path.join(args.labels, "*.json")):
    d = json.load(open(fp)); n += 1
    for x in d["labels"]:
        u = units[x["idx"]]
        total[x["event"]] += 1
        by_role[x["event"]][u["role"]] += 1
        r = u["round"]
        by_round[x["event"]][r if r is not None and r <= 3 else 3] += 1
N = sum(total.values())
print(f"{n} games, {N} labeled sentences\n")
print(f"{'event':<26}{'N':>7}{'share':>7}   M/V/D%   R0/R1/R2/R3%")
for e, c in total.most_common():
    r = by_role[e]; t = sum(r.values()) or 1
    rd = by_round[e]; td = sum(rd.values()) or 1
    print(f"{e:<26}{c:>7}{c/N*100:>6.1f}%   "
          f"{r['Mafia']*100//t}/{r['Villager']*100//t}/{r['Doctor']*100//t}   "
          f"{'/'.join(str(rd[i]*100//td) for i in range(4))}")
