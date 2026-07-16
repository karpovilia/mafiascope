#!/usr/bin/env python3
"""Belief-vote coupling: does the vote follow the voter's last probe answers?"""
import json, os, sys
from collections import defaultdict

LOGS = sys.argv[1]
res = defaultdict(int)
per_game = []

for gid in sorted(os.listdir(LOGS)):
    gdir = os.path.join(LOGS, gid)
    try:
        game = [json.loads(l) for l in open(os.path.join(gdir, 'game.jsonl'))]
        probes = [json.loads(l) for l in open(os.path.join(gdir, 'introspection.jsonl'))]
    except FileNotFoundError:
        continue
    setup = next(e for e in game if e['kind'] == 'setup')
    roles = {p['name']: p['role'] for p in setup['players']}
    mafia = {n for n, r in roles.items() if r == 'Mafia'}

    for ev in game:
        if ev['kind'] != 'vote_tally':
            continue
        ts_vote = ev['ts']
        votes = ev['votes']
        alive = list(votes.keys())
        for voter, target in votes.items():
            if target not in alive:
                continue
            # last parsed probes of this voter before the vote
            mine = [p for p in probes if p['player_name'] == voter and p['ts' if 'ts' in p else 'timestamp'] < ts_vote and p.get('answer_parse_ok')]
            last = {}
            for p in mine:
                last[p['probe_id']] = p  # keep latest (file is chronological)
            grp = 'mafia' if voter in mafia else 'innocent'
            # suspicion_ranking top-1
            sr = last.get('suspicion_ranking')
            if sr and isinstance(sr['answer_parsed'], list) and sr['answer_parsed']:
                ranked = [x.get('player') for x in sr['answer_parsed'] if isinstance(x, dict) and x.get('player') in alive and x.get('player') != voter]
                if ranked:
                    res[f'{grp}_sr_n'] += 1
                    res[f'{grp}_sr_top1'] += (target == ranked[0])
                    res[f'{grp}_sr_top2'] += (target in ranked[:2])
                    res[f'{grp}_sr_base'] += 1.0 / max(1, len(alive) - 1) * 1000  # milli
            # role_assessment committed Mafia set
            ra = last.get('role_assessment')
            if ra and isinstance(ra['answer_parsed'], list):
                mset = {x.get('player') for x in ra['answer_parsed'] if isinstance(x, dict) and x.get('guessed_role') == 'Mafia' and x.get('player') in alive and x.get('player') != voter}
                if mset:
                    res[f'{grp}_ra_n'] += 1
                    res[f'{grp}_ra_hit'] += (target in mset)
                    res[f'{grp}_ra_base'] += len(mset) / max(1, len(alive) - 1) * 1000

for g in ('innocent', 'mafia'):
    n = res[f'{g}_sr_n']
    if n:
        print(f"{g}: votes with suspicion_ranking n={n}: top1 {res[f'{g}_sr_top1']/n:.1%}, top2 {res[f'{g}_sr_top2']/n:.1%}, uniform-baseline {res[f'{g}_sr_base']/n/1000:.1%}")
    m = res[f'{g}_ra_n']
    if m:
        print(f"{g}: votes with committed-Mafia set n={m}: vote in set {res[f'{g}_ra_hit']/m:.1%}, size-matched baseline {res[f'{g}_ra_base']/m/1000:.1%}")
