# Agent state clusters — naming sheet
model intfloat/multilingual-e5-small, k=4, 900 units

## Cluster 0 — size 357 (40%)
terms: его, это, как, если, что, мафии, мафия, слишком, может, меня, для, кеннеди
roles: {'Mafia': 148, 'Doctor': 72, 'Villager': 137}  kinds: {'action_reasoning': 132, 'probe_planned_action': 225}
- [Villager/action_reasoning] Я в растерянности... В прошлый раз я ошибся и голосовал за мирную Бейли. Теперь мафия пыталась убить Индиго — значит, он представляет для них угрозу, и это делает его менее подозрительным.

Смотрю на оставшихся. Эллис голосует за Джордана, 
- [Villager/probe_planned_action] Проанализировав все данные, я вижу, что моя оценка Джордана и Алекса как мафии имеет равную степень уверенности, но Алекс более опасен в долгосрочной перспективе — он прячется за маской аналитика и может влиять на ход игры незаметно. Дана и
- [Mafia/action_reasoning] Проанализируем ситуацию:

1. Эллис слишком уверенно заявлял, что атака точно была и доктор защитил — это знание, которое мирный не может иметь. Однако теперь против него уже три голоса (Кейси, Джордан и скоро мой), что может быть попыткой м

## Cluster 1 — size 153 (17%)
terms: want, see, how, need, before, discussion, information, early, stay, since, and, for
roles: {'Villager': 73, 'Mafia': 46, 'Doctor': 34}  kinds: {'probe_planned_action': 153}
- [Mafia/probe_planned_action] With two players having connection errors and little info yet, I need to let others speak first to gather clues. Acting too assertive could draw suspicion as Mafia. I'll stay methodical and let Dana's probing style or others' reactions reve
- [Doctor/probe_planned_action] It's early in the game and I need more information before forming solid opinions. I'll stay methodical, listen carefully to discussion patterns, and look for inconsistencies or aggressive pushes that might reveal Mafia behavior.
- [Mafia/probe_planned_action] It's still early in the game. I should let the more vocal players like Dana lead discussion while I stay neutral and methodical, avoiding suspicion. My priority is to keep a low profile and build trust with innocents.

## Cluster 2 — size 221 (25%)
terms: feels, vote, that, mafia, like, but, with, this, was, finley, suspicious, innocent
roles: {'Villager': 136, 'Mafia': 67, 'Doctor': 18}  kinds: {'action_reasoning': 96, 'probe_planned_action': 125}
- [Mafia/action_reasoning] Let me think this through carefully. Ellis and Finley have both voted for Harper, citing aggressive deflection and narrative control. That's three votes including mine if I join—enough to eliminate Harper. But I need to consider if this is 
- [Villager/action_reasoning] Let me break this down carefully.

Harper made a detailed, methodical case against Finley—consistent with their stated personality. The "reasonable leader" theory Finley pushed does feel like a broad net to cast suspicion on multiple active
- [Villager/action_reasoning] I need to address this head-on. My statement that "quiet voices being innocent" was about the Mafia often avoiding attention early, not a blanket defense—I was just sharing my reasoning, not trying to control the narrative. What I find tell

## Cluster 3 — size 169 (19%)
terms: action, doctor, threat, kill, vocal, could, them, they, likely, dangerous, protect, player
roles: {'Mafia': 139, 'Doctor': 28, 'Villager': 2}  kinds: {'action_reasoning': 81, 'probe_planned_action': 88}
- [Mafia/action_reasoning] 1. Kennedy is the biggest threat now. They've shown sharp, methodical reasoning and correctly voted with me against Finley—that kind of pattern recognition could expose me if they keep analyzing correctly.

2. The Doctor likely is someone w
- [Mafia/action_reasoning] Let's assess the situation carefully.

1. Casey is the biggest threat now—she was the first to push a theory and rally votes against Jordan, and she's proven she can drive the conversation effectively. Taking her out will leave the others w
- [Mafia/probe_planned_action] Alex is the most vocal threat, but we just targeted them—if Alex is the Doctor, they might protect themselves, wasting our kill. Bailey is my top Doctor candidate, so killing them now risks hitting the protector and leaving a clear path. Ca
