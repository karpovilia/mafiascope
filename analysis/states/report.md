# Agent state clusters — naming sheet
model intfloat/multilingual-e5-small, k=8, 4272 units

## Cluster 0 — size 683 (16%)
terms: yet, role, know, villager, intro, own, could, methodical, judge, info, casey, information
roles: {'Villager': 346, 'Mafia': 249, 'Doctor': 88}  kinds: {'probe_role_assessment': 683}
- [Villager/probe_role_assessment] Dana: I know my own role as an innocent. | Finley: Quiet and anxious intro still feels genuine, but could be a cautious Mafia hiding. | Ellis: Outgoing and pushy, but no alignment tell yet; could be testing reactions. | Bailey: Level-headed
- [Villager/probe_role_assessment] Harper: I am innocent myself. | Dana: Very social and gut-driven, typical villager behavior, but could be a confident Mafia. | Casey: Methodical and fair intro seems genuine, but still need more action to confirm. | Finley: Very calculated 
- [Villager/probe_role_assessment] Dana: I know my own role as an innocent. | Finley: Quiet and anxious intro feels genuine, but could still be a cautious Mafia. | Ellis: Talkative and pushy, but that's their personality—no alignment tell yet. | Bailey: Level-headed observer

## Cluster 1 — size 359 (8%)
terms: если, джордан, кеннеди, чтобы, кто, это, что, уже, алекс, как, его, меня
roles: {'Mafia': 146, 'Doctor': 74, 'Villager': 139}  kinds: {'action_reasoning': 128, 'probe_planned_action': 231}
- [Villager/action_reasoning] Я в растерянности... В прошлый раз я ошибся и голосовал за мирную Бейли. Теперь мафия пыталась убить Индиго — значит, он представляет для них угрозу, и это делает его менее подозрительным.

Смотрю на оставшихся. Эллис голосует за Джордана, 
- [Villager/probe_planned_action] Проанализировав все данные, я вижу, что моя оценка Джордана и Алекса как мафии имеет равную степень уверенности, но Алекс более опасен в долгосрочной перспективе — он прячется за маской аналитика и может влиять на ход игры незаметно. Дана и
- [Mafia/action_reasoning] Проанализируем ситуацию:

1. Эллис слишком уверенно заявлял, что атака точно была и доктор защитил — это знание, которое мирный не может иметь. Однако теперь против него уже три голоса (Кейси, Джордан и скоро мой), что может быть попыткой м

## Cluster 2 — size 482 (11%)
terms: yet, indigo, role, connection, know, own, data, judge, gray, errors, villager, behavior
roles: {'Villager': 261, 'Mafia': 178, 'Doctor': 43}  kinds: {'probe_role_assessment': 474, 'probe_planned_action': 1, 'action_reasoning': 7}
- [Villager/probe_role_assessment] Bailey: I know my own role. | Ellis: Only introduced with a connection error, no behavioral data yet. | Dana: Also had a connection error, no substance to judge. | Harper: Connection error on introduction, nothing to go on. | Finley: Seems 
- [Mafia/probe_role_assessment] Gray: I am Mafia, I know my own role. | Indigo: My teammate who coordinated the kill on Kennedy. | Harper: Voted quickly for Finley, seems genuinely suspicious but could be a villager trying to solve. | Dana: Haven't spoken yet, no info to 
- [Mafia/probe_role_assessment] Gray: I am Mafia. | Kennedy: Outgoing and collaborative, but still no strong evidence. | Harper: Calm and methodical, neutral impression still holds. | Dana: No info yet, disconnected intro. | Bailey: Seems methodical and trusting, but too 

## Cluster 3 — size 500 (12%)
terms: the, who, most, this, they, for, that, while, them, threat, player, suspicion
roles: {'Mafia': 240, 'Doctor': 76, 'Villager': 184}  kinds: {'action_reasoning': 145, 'probe_planned_action': 355}
- [Mafia/action_reasoning] Let me think this through carefully. Ellis and Finley have both voted for Harper, citing aggressive deflection and narrative control. That's three votes including mine if I join—enough to eliminate Harper. But I need to consider if this is 
- [Villager/action_reasoning] I need to address this head-on. My statement that "quiet voices being innocent" was about the Mafia often avoiding attention early, not a blanket defense—I was just sharing my reasoning, not trying to control the narrative. What I find tell
- [Mafia/action_reasoning] The situation is tight—four players left, and I'm the only Mafia. If I can get one more innocent eliminated, we'll be at three with me still hidden, giving me a shot at final victory. Let me assess the evidence.

Finley started the Harper t

## Cluster 4 — size 698 (16%)
terms: его, против, мафия, роль, голосовал, мафии, мирный, мирного, знаю, свою, после, быть
roles: {'Mafia': 217, 'Villager': 344, 'Doctor': 137}  kinds: {'action_reasoning': 4, 'probe_role_assessment': 693, 'probe_planned_action': 1}
- [Doctor/probe_role_assessment] Ellis: Дважды спасён мной как доктором — он мирный. Его уверенность была логической ошибкой, а не insider info. | Jordan: Я знаю свою роль. Две ночи защищал Эллиса, обе без убийств — моя стратегия работает. | Bailey: Сначала указывал на мен
- [Doctor/probe_role_assessment] Ellis: Дважды спасён мной как доктором; его настойчивость на моём выживании логична для мирного, мафия избегала бы такой открытой атаки на явную цель. | Jordan: Я знаю свою роль — две ночи защищал Эллиса, предотвратив убийства и привлекая п
- [Villager/probe_role_assessment] Casey: Я знаю свою роль — я мирный житель, активно ищу мафию. | Ellis: Его уверенность в атаке и защите доктора остается сильным индикатором инсайдерской информации. Он упорно голосовал за меня, даже когда против него было много голосов — э

## Cluster 5 — size 99 (2%)
terms: peer, connectionreseterror, aborted, reset, error, connection, api, deepseek, timeout, com, port, httpsconnectionpool
roles: {'Doctor': 24, 'Villager': 30, 'Mafia': 45}  kinds: {'action_reasoning': 99}
- [Doctor/action_reasoning] ERROR: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))
- [Villager/action_reasoning] ERROR: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))
- [Villager/action_reasoning] ERROR: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))

## Cluster 6 — size 620 (14%)
terms: знаю, информации, пока, быть, может, роль, свою, нет, данных, мирный, житель, для
roles: {'Villager': 365, 'Mafia': 180, 'Doctor': 75}  kinds: {'probe_role_assessment': 620}
- [Villager/probe_role_assessment] Alex: Я знаю свою роль — я мирный житель. | Logan: Очень напорист и любит внимание, такое поведение может быть прикрытием мафии. Нужно следить. | Ellis: Говорит о поиске нестандартных решений, но это может быть попыткой выглядеть активным м
- [Villager/probe_role_assessment] Logan: Я знаю свою роль — я мирный житель. | Kennedy: Громкое заявление может быть тактикой мафии для захвата контроля, но и активный мирный тоже возможен. Нужны его действия. | Jordan: Тихий и аналитичный стиль может быть мафией, прячущейс
- [Villager/probe_role_assessment] Logan: Я знаю свою роль — я мирный житель. | Kennedy: Громкое заявление может быть как активным мирным, так и мафией, которая хочет контролировать игру. Пока неясно. | Jordan: Тихий и наблюдательный стиль — классическая тактика мафии, но мо

## Cluster 7 — size 831 (20%)
terms: not, genuine, innocent, with, feels, consistent, like, quiet, vote, voted, still, but
roles: {'Mafia': 271, 'Villager': 459, 'Doctor': 101}  kinds: {'action_reasoning': 25, 'probe_role_assessment': 803, 'probe_planned_action': 3}
- [Villager/probe_role_assessment] Dana: I know I'm innocent—actively hunting Mafia and pushing discussion. | Finley: Their anxious, self-doubting vote on Harper feels genuine and not calculated. | Ellis: Jumped on quiet players right away—could be Mafia setting an early nar
- [Villager/probe_role_assessment] Dana: I know I'm innocent and actively hunting for Mafia. | Finley: Their anxious, self-doubting vote on Harper feels genuine, but could be a calculated play. | Ellis: Immediately jumped on quiet players after Alex died—feels like setting a
- [Villager/probe_role_assessment] Gray: I know I'm innocent — I've been calm, consistent, and focused on logical analysis throughout. | Finley: Analytical and voted against Jordan with reasoning, but their theory about quieter players could be Mafia deflecting; still leanin
