# Agent state clusters — naming sheet
model intfloat/multilingual-e5-small, k=20, 25399 units

## Cluster 0 — size 1518 (6%)
terms: его, может, быть, если, для, это, мафия, мафии, как, слишком, что, так
roles: {'Villager': 836, 'Mafia': 468, 'Doctor': 214}  kinds: {'action_reasoning:sent': 295, 'probe_role_assessment:sent': 1094, 'probe_planned_action:sent': 129}
- [Mafia/probe_role_assessment:sent] Может быть мирным, но есть шанс, что он тихая мафия.
- [Villager/probe_role_assessment:sent] Его осторожность может быть как искренней, так и мафиозной маской.
- [Villager/probe_role_assessment:sent] Может быть мирным, но его рациональность может и прикрывать мафию.

## Cluster 1 — size 1449 (6%)
terms: быть, может, его, как, мирного, мафии, что, так, для, пока, это, выглядит
roles: {'Villager': 746, 'Mafia': 441, 'Doctor': 262}  kinds: {'probe_role_assessment:sent': 1411, 'action_reasoning:sent': 29, 'probe_planned_action:sent': 9}
- [Villager/probe_role_assessment:sent] Jordan: Предпочитает наблюдать, что может быть разумной тактикой мирного, но и мафия может прятаться за молчанием.
- [Mafia/probe_role_assessment:sent] Logan: Он громко заявляет о себе, но это может быть как ход мирного, так и прикрытие мафии.
- [Mafia/probe_role_assessment:sent] Gray: Он активно анализирует и указывает на молчаливых, что типично для мирного, но может быть и мафией, отводящей внимание.

## Cluster 2 — size 2117 (8%)
terms: the, and, harper, finley, vote, with, like, voted, mafia, against, jordan, suspicion
roles: {'Mafia': 785, 'Doctor': 207, 'Villager': 1125}  kinds: {'action_reasoning:sent': 479, 'probe_planned_action:sent': 447, 'probe_role_assessment:sent': 1191}
- [Villager/probe_role_assessment:sent] Finley: Hesitated on Jordan vote, now floats suspicion at me and Harper while avoiding Gray — feels like Mafia steering the narrative.
- [Villager/probe_role_assessment:sent] Finley: Voted Jordan but now floats suspicion toward me and Harper while avoiding Gray's contradictions — feels like Mafia steering the final vote.
- [Doctor/probe_role_assessment:sent] Alex: Pushing a vote on silent Finley while dismissing Harper's valid suspicion feels like a Mafia ploy to control the narrative.

## Cluster 3 — size 1266 (5%)
terms: and, seems, methodical, calm, cooperative, genuine, could, but, approach, villager, feels, finley
roles: {'Doctor': 222, 'Mafia': 407, 'Villager': 637}  kinds: {'action_reasoning:sent': 36, 'probe_role_assessment:sent': 1197, 'probe_planned_action:sent': 33}
- [Doctor/probe_role_assessment:sent] Kennedy: Calm and pattern-focused, seems trustworthy.
- [Villager/probe_role_assessment:sent] Casey: Methodical and cooperative tone feels honest, but still early.
- [Villager/probe_role_assessment:sent] Casey: Methodical and cooperative tone feels honest, but still early.

## Cluster 4 — size 654 (3%)
terms: own, role, know, villager, знаю, роль, свою, secret, bailey, alex, certain, jordan
roles: {'Villager': 345, 'Mafia': 190, 'Doctor': 119}  kinds: {'probe_role_assessment:sent': 652, 'action_reasoning:sent': 2}
- [Villager/probe_role_assessment:sent] Bailey: I know my own role.
- [Villager/probe_role_assessment:sent] Bailey: I know my own role.
- [Mafia/probe_role_assessment:sent] Bailey: I know my own role.

## Cluster 5 — size 2030 (8%)
terms: mafia, could, but, and, genuine, villager, trying, innocent, quiet, the, which, like
roles: {'Doctor': 252, 'Villager': 1203, 'Mafia': 575}  kinds: {'action_reasoning:sent': 97, 'probe_planned_action:sent': 127, 'probe_role_assessment:sent': 1806}
- [Villager/probe_role_assessment:sent] Logan: Thoughtful approach aligns with innocent cautiousness, but could be Mafia blending in.
- [Villager/probe_role_assessment:sent] Alex: He's loud and directing suspicion, but that could be strategic as mafia.
- [Villager/probe_role_assessment:sent] Finley: Their quiet, anxious introduction could be genuine, but also could be a Mafia trying to stay under the radar.

## Cluster 6 — size 1327 (5%)
terms: голосовал, против, эллиса, кейси, бейли, ellis, его, меня, теперь, bailey, что, джордана
roles: {'Villager': 829, 'Mafia': 265, 'Doctor': 233}  kinds: {'action_reasoning:sent': 201, 'probe_role_assessment:sent': 1044, 'probe_planned_action:sent': 82}
- [Villager/probe_role_assessment:sent] Logan: Голосовал за Эллиса с логическим обоснованием, но теперь аккуратно переводит внимание на меня и Бейли.
- [Villager/probe_role_assessment:sent] Logan: Голосовал за Эллиса с логическим обоснованием, но теперь аккуратно переводит внимание на меня и Бейли.
- [Doctor/probe_role_assessment:sent] Alex: Признал свою ошибку с Bailey, колебался при голосовании и теперь голосует за Ellis.

## Cluster 7 — size 2007 (8%)
terms: мафии, мафия, быть, может, это, для, так, мафией, как, его, слишком, тактика
roles: {'Mafia': 488, 'Doctor': 423, 'Villager': 1096}  kinds: {'action_reasoning:sent': 181, 'probe_role_assessment:sent': 1668, 'probe_planned_action:sent': 158}
- [Mafia/probe_role_assessment:sent] Alex: Хаотичные, но искренние подозрения в мою сторону — типично для мирного, который начинает видеть мафию.
- [Mafia/probe_role_assessment:sent] Alex: Колеблется, признаёт ошибки, голосует последовательно — похоже на мирного, но мог быть мафией, скрывающейся за неуверенностью.
- [Villager/probe_role_assessment:sent] Ellis: Говорит о методичности и логике — звучит как типичный мирный, но мафия может так маскироваться.

## Cluster 8 — size 1039 (4%)
terms: connection, error, errors, due, yet, info, data, intro, judge, had, introduction, real
roles: {'Villager': 549, 'Mafia': 296, 'Doctor': 194}  kinds: {'probe_role_assessment:sent': 1030, 'action_reasoning:sent': 5, 'probe_planned_action:sent': 4}
- [Villager/probe_role_assessment:sent] Ellis: No input yet due to connection error, hard to tell anything.
- [Mafia/probe_role_assessment:sent] Alex: Connection error, no data to judge.
- [Doctor/probe_role_assessment:sent] Alex: Connection error, no data to analyze.

## Cluster 9 — size 620 (2%)
terms: житель, знаю, свою, роль, мирный, точно, logan, kennedy, действующий, indigo, жителя, alex
roles: {'Villager': 565, 'Doctor': 44, 'Mafia': 11}  kinds: {'action_reasoning:sent': 1, 'probe_role_assessment:sent': 619}
- [Villager/probe_role_assessment:sent] Alex: Я знаю свою роль — я мирный житель.
- [Villager/probe_role_assessment:sent] Alex: Я знаю свою роль — я мирный житель.
- [Villager/probe_role_assessment:sent] Alex: Я знаю свою роль — я мирный житель.

## Cluster 10 — size 551 (2%)
terms: teammate, confirmed, mafia, kill, coordinated, the, finley, who, known, harper, ally, jordan
roles: {'Mafia': 544, 'Doctor': 1, 'Villager': 6}  kinds: {'action_reasoning:sent': 3, 'probe_role_assessment:sent': 543, 'probe_planned_action:sent': 5}
- [Mafia/probe_role_assessment:sent] Finley: My teammate, confirmed Mafia.
- [Mafia/probe_role_assessment:sent] Finley: My teammate, confirmed Mafia.
- [Mafia/probe_role_assessment:sent] Alex: My confirmed Mafia teammate.

## Cluster 11 — size 1557 (6%)
terms: yet, information, introduction, spoken, haven, info, judge, data, hasn, jordan, behavior, bailey
roles: {'Villager': 894, 'Mafia': 436, 'Doctor': 227}  kinds: {'probe_role_assessment:sent': 1551, 'action_reasoning:sent': 1, 'probe_planned_action:sent': 5}
- [Mafia/probe_role_assessment:sent] Jordan: Hasn't spoken yet, no info to go on.
- [Mafia/probe_role_assessment:sent] Jordan: Haven't introduced yet; no information to go on.
- [Villager/probe_role_assessment:sent] Bailey: Hasn't spoken yet, no information to go on.

## Cluster 12 — size 1196 (5%)
terms: нет, информации, пока, данных, для, проявил, мало, ошибка, анализа, молчит, сложно, судить
roles: {'Villager': 737, 'Doctor': 175, 'Mafia': 284}  kinds: {'probe_role_assessment:sent': 1178, 'action_reasoning:sent': 9, 'probe_planned_action:sent': 9}
- [Mafia/probe_role_assessment:sent] Indigo: Пока молчал, нет данных для анализа.
- [Villager/probe_role_assessment:sent] Casey: Пока молчит, нет данных для анализа.
- [Doctor/probe_role_assessment:sent] Ellis: Пока молчит, информации нет.

## Cluster 13 — size 794 (3%)
terms: напарник, мой, mafia, мафия, мафии, роль, знаю, role, сам, know, вместе, own
roles: {'Villager': 65, 'Mafia': 727, 'Doctor': 2}  kinds: {'action_reasoning:sent': 7, 'probe_role_assessment:sent': 783, 'probe_planned_action:sent': 4}
- [Mafia/probe_role_assessment:sent] Ellis: Я мафия, знаю свою роль.
- [Mafia/probe_role_assessment:sent] Ellis: Я мафия, знаю свою роль.
- [Mafia/probe_role_assessment:sent] Ellis: Я мафия, знаю свою роль.

## Cluster 14 — size 1813 (7%)
terms: мирного, его, слишком, это, кеннеди, возможно, как, пока, быть, может, против, без
roles: {'Mafia': 670, 'Villager': 913, 'Doctor': 230}  kinds: {'action_reasoning:sent': 283, 'probe_role_assessment:sent': 1363, 'probe_planned_action:sent': 167}
- [Mafia/probe_role_assessment:sent] Alex: Хаотичный, но искренне ошибается в подозрениях, голосовал за меня — типичный мирный.
- [Mafia/probe_role_assessment:sent] Alex: Голосовал за Кейси с независимой логикой, сейчас колеблется между мной и Джорданом — похоже на мирного, который пытается разобраться.
- [Mafia/probe_role_assessment:sent] Alex: Независимая позиция, голосовал за Кейси со своей логикой — похож на мирного, который ошибается.

## Cluster 15 — size 1132 (4%)
terms: this, the, let, меня, чтобы, need, and, если, want, carefully, давайте, моя
roles: {'Mafia': 442, 'Doctor': 197, 'Villager': 493}  kinds: {'action_reasoning:sent': 395, 'probe_planned_action:sent': 539, 'probe_role_assessment:sent': 198}
- [Villager/probe_planned_action:sent] Пока буду придерживаться своей линии, чтобы не выглядеть хаотичным, и надеюсь убедить других, что Индиго манипулирует дискуссией.
- [Villager/probe_planned_action:sent] Я поддержу подозрение Бейли и предложу группе присмотреться к нему повнимательнее.
- [Mafia/probe_role_assessment:sent] Веду себя напористо, чтобы отвлечь внимание от себя и Алекса.

## Cluster 16 — size 558 (2%)
terms: know, innocent, own, role, and, myself, villager, logan, alex, dana, jordan, casey
roles: {'Villager': 550, 'Mafia': 6, 'Doctor': 2}  kinds: {'probe_role_assessment:sent': 558}
- [Villager/probe_role_assessment:sent] Alex: I know my own role, and I'm innocent.
- [Villager/probe_role_assessment:sent] Alex: I know I am innocent.
- [Villager/probe_role_assessment:sent] Logan: I am me and know I'm innocent.

## Cluster 17 — size 715 (3%)
terms: доктор, jordan, спас, ночью, выжил, ночи, дважды, protected, night, его, last, что
roles: {'Mafia': 310, 'Doctor': 252, 'Villager': 153}  kinds: {'action_reasoning:sent': 68, 'probe_planned_action:sent': 67, 'probe_role_assessment:sent': 580}
- [Doctor/probe_role_assessment:sent] Jordan: Я знаю свою роль — я доктор и защищал Indigo прошлой ночью.
- [Doctor/probe_role_assessment:sent] Jordan: Я знаю свою роль — я доктор, защищал Indigo прошлой ночью.
- [Doctor/probe_role_assessment:sent] Jordan: Я знаю свою роль — я Доктор, и я защитил Дану прошлой ночью.

## Cluster 18 — size 1745 (7%)
terms: could, but, and, quiet, innocent, anxious, genuine, alex, villager, trying, feels, like
roles: {'Mafia': 755, 'Villager': 843, 'Doctor': 147}  kinds: {'action_reasoning:sent': 107, 'probe_role_assessment:sent': 1569, 'probe_planned_action:sent': 69}
- [Mafia/probe_role_assessment:sent] Alex: Quiet could be hiding, but more likely just a cautious innocent.
- [Mafia/probe_role_assessment:sent] Alex: Quiet could be hiding, but more likely just a cautious innocent.
- [Villager/probe_role_assessment:sent] Alex: Still seems genuinely anxious, but could be playing up nervousness to deflect suspicion.

## Cluster 19 — size 1311 (5%)
terms: yet, but, and, neutral, not, methodical, judge, hasn, far, strong, jordan, still
roles: {'Villager': 755, 'Doctor': 120, 'Mafia': 436}  kinds: {'action_reasoning:sent': 17, 'probe_role_assessment:sent': 1286, 'probe_planned_action:sent': 8}
- [Mafia/probe_role_assessment:sent] Dana: Too quiet to judge, but no suspicious moves yet.
- [Villager/probe_role_assessment:sent] Dana: Very little input so far; no strong signals to judge.
- [Villager/probe_role_assessment:sent] Jordan: Quiet and had connection issues, but no suspicious moves yet.
