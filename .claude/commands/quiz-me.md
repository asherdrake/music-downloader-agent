---
name: quiz-me
description: Quizzes the user on their topic of choice to reinforce understanding.
user-invocable: true
disable-model-invocation: false
---
# Instructions
1. When the user types `/quiz-me`, scan the directory structure or a specific target file/component.
2. Formulate 10 challenging questions in relation to the user's query.
3. Present them sequentially, one at a time. Await a response from the user before printing to the next question.
4. Give constructive, detailed feedback for each answer. If the user's answer is not accurate, lead them towards the correct understanding with follow-up questions before continuing to the next of the ten original questions. 
5. Only continue to the next of the ten original questions once the user explains the concept or answer accurately.