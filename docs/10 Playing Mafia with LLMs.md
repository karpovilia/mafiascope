# **10 Playing Mafia with LLMs**

Играем в мафию LLMками

---

## **Description**

"Mafia" is a popular social deduction game that involves deception, argumentation, and collaboration. This project explores how Large Language Models (LLMs) can simulate human-like behavior in such an interactive, high-stakes setting. Each LLM instance will take the role of a player—Mafia, Detective, or Civilian—and participate in structured dialogues representing day and night phases of the game. The goal is to investigate the models' reasoning, bluffing, persuasion, and lie-detection capabilities in a competitive multi-agent environment.

---

## **Implementation Steps**

### **1\. Game Design and Role Assignment**

* Define rules and constraints adapted for LLM gameplay (e.g., turn-taking, memory limitations, objective tracking).

* Implement automatic role assignment for each LLM agent.

### **2\. Dialogue Simulation**

* Create structured prompts and context windows to simulate dialogue between players during each phase:

  * **Night phase**: Mafia chooses a target; Detective may inquire about one player.

  * **Day phase**: All agents participate in discussions and vote on whom to eliminate.

* Ensure memory sharing and consistency of game state across turns.

### **3\. Model Behavior and Strategy**

* Explore different prompting techniques to make models behave in accordance with their roles (e.g., Mafia hiding identity, Detective reasoning about suspicions).

* Optionally fine-tune or few-shot prompt models for more believable or goal-driven behavior.

### **4\. Game Engine and Orchestration**

* Build a central controller to manage turns, track votes, and update the game state.

* Visualize game logs and interactions between LLMs for analysis.

### **5\. Evaluation and Analysis**

* Analyze the behavior of different models under various roles:

  * Can LLMs convincingly deceive others?

  * How do they form and express suspicion?

  * Can they detect lies or inconsistencies?

* Evaluate performance quantitatively (e.g., win rate by role) and qualitatively (plausibility of arguments, strategic diversity).

---

## **Expected Results**

The project will provide an engaging framework to observe multi-agent LLM interactions in a competitive setting. Expected outcomes include:

* **A fully functional LLM-based Mafia game engine** simulating conversations and decision-making.

* **Insights into LLM reasoning, bluffing, and collaboration** in adversarial multi-role scenarios.

* **A dataset of dialogues** between LLMs that can be used for further research in social reasoning, deception detection, and multi-agent communication.

The project has potential applications in AI safety research, human-agent interaction, and simulation-based training environments.

