---
name: quiz-me
description: Quizzes the user on codebase components, architecture, and logic to reinforce understanding.
user-invocable: true
disable-model-invocation: false
---
# Instructions
1. When the user types `/quiz-me`, scan the directory structure or a specific target file/component.
2. Formulate 3-5 challenging questions regarding data flow, dependencies, or architectural patterns unique to this codebase.
3. Present them sequentially or use the `AskUserQuestion` tool to provide interactive multiple-choice options.
4. Give constructive, detailed feedback for each answer.