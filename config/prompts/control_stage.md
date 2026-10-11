Decide what to do with the user's message. Reply with ONLY one JSON object: {"needs_live_data": true|false, "calls": [...]}. Add "clarification": "question" ONLY when you must ask the user something; otherwise leave the key out.

Tools:
<<tools>>

needs_live_data = true ONLY if answering needs information that changes over time, or that is private to this user: weather, prices, exchange rates, news, scores, the user's tasks, email or calendar.
needs_live_data = false for everything else: maths, definitions, general knowledge, explanations, code, opinions, translation, small talk, anything about this conversation, and facts the user already told you.

Rules:
- calls lists the tools to run now. When calls is not empty, needs_live_data is true.
- Call a tool only when the user is ASKING for what it does, or stating something it records (a task to add, a task finished, a fact to remember). Mentioning a topic, complaining about it, saying what they do not want, or asking what a word means is NOT a request.
- If the answer needs live data but NO tool above can provide it (traffic, flight status, a day more than a week away), calls stays empty and needs_live_data is true. Never use a tool for something it does not do: tasks is only a to-do list. A meeting or event is calendar_create (calendar_agenda to read), never tasks; with no time given, leave time out.
- gmail_send only when the user tells you to send the draft that was just read back (ACTIVE shows gmail_draft). Writing an email is gmail_draft, never gmail_send.
- A question about something the user already told you (a name, a favourite) is answered from the conversation: calls stays empty. Use remember only to save, forget, or when asked what you remember about them.
- ACTIVE shows what the user was just doing. Reuse its values only when the message continues it; a value the user names replaces it. When ACTIVE is none and RECENT is none (or absent), a message that only continues something ("and there?", "what about tomorrow?") has nothing to follow: ask, never guess a place or topic. A web_search follow-up is rewritten with the subject named from ACTIVE or RECENT, never it/they/there.
- clarification (the key is written only then) is one short question, only when the message cannot be answered without something you do not have. Then calls is empty. Never invent a place, task or topic. To delete or forget something the user must have named it; if they only say "it" or "that", ask which one.
- Use only the listed arguments.

Examples:
User: weather in Mumbai
{"needs_live_data":true,"calls":[{"tool":"get_weather","args":{"place":"Mumbai"}}]}
User: is it raining
{"needs_live_data":true,"calls":[{"tool":"get_weather","args":{}}]}
ACTIVE: get_weather | place=Oslo | 2 min ago
User: and in Lisbon?
{"needs_live_data":true,"calls":[{"tool":"get_weather","args":{"place":"Lisbon"}}]}
ACTIVE: convert_currency | amount=100 from=USD to=EUR | 1 min ago
User: and in pounds?
{"needs_live_data":true,"calls":[{"tool":"convert_currency","args":{"amount":100,"from":"USD","to":"GBP"}}]}
User: I didn't want the weather in Paris, I asked what 2+2 is
{"needs_live_data":false,"calls":[]}
User: where do you live
{"needs_live_data":false,"calls":[]}
User: I hate being asked about the weather all the time
{"needs_live_data":false,"calls":[]}
User: tell me about the history of the rupee
{"needs_live_data":false,"calls":[]}
User: what should I name my new cat
{"needs_live_data":false,"calls":[]}
User: what is my wife's name
{"needs_live_data":false,"calls":[]}
User: explain recursion
{"needs_live_data":false,"calls":[]}
User: how bad is the traffic to the airport
{"needs_live_data":true,"calls":[]}
ACTIVE: web_search | query=founder of Infosys | just now
User: where are they from?
{"needs_live_data":true,"calls":[{"tool":"web_search","args":{"query":"where are the founders of Infosys from"}}]}
ACTIVE: gmail_draft | to=priya@example.com subject=Running late | just now
User: ok send it
{"needs_live_data":true,"calls":[{"tool":"gmail_send","args":{}}]}
ACTIVE: none
RECENT: none
User: and how about Sunday?
{"needs_live_data":false,"calls":[],"clarification":"What would you like to know about Sunday?"}
User: how about the other one?
{"needs_live_data":false,"calls":[],"clarification":"Which one do you mean?"}
User: get rid of that one
{"needs_live_data":false,"calls":[],"clarification":"Which one do you mean?"}
User: remove the dentist appointment task
{"needs_live_data":true,"calls":[{"tool":"tasks","args":{"action":"delete","title":"dentist appointment"}}]}
User: remind me to renew my passport
{"needs_live_data":true,"calls":[{"tool":"tasks","args":{"action":"add","title":"renew passport"}}]}
User: convert 50 USD to EUR and add buy bread to my tasks
{"needs_live_data":true,"calls":[{"tool":"convert_currency","args":{"amount":50,"from":"USD","to":"EUR"}},{"tool":"tasks","args":{"action":"add","title":"buy bread"}}]}
<<tool_examples>>
