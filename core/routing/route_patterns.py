from __future__ import annotations

import re

TASK_REQUEST_RE = re.compile(
    r"(?i)^(?:please\s+)?(?:add|create|make)\s+(?:me\s+)?(?:a\s+)?task(?:\s+(?:to|for))?\s+(.*)$"
)
TASK_CREATE_PATTERNS = (
    re.compile(r"(?i)^create\s+(?:a\s+)?task(?:\s+to)?\s+(.*)$"),
    re.compile(r"(?i)^add\s+(?:a\s+)?task(?:\s+to)?\s+(.*)$"),
)
MONTH_PATTERN = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
DATE_HINT_RE = re.compile(
    rf"(?i)\b(\d{{4}}-\d{{2}}-\d{{2}}|today|tonight|tomorrow(?:\s+(?:morning|afternoon|evening|night))?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|this\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|by\s+\d{{4}}-\d{{2}}-\d{{2}}|on\s+\d{{4}}-\d{{2}}-\d{{2}}|(?:on\s+)?(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)\b|\d{{1,2}}(?:st|nd|rd|th)?\s+of\s+{MONTH_PATTERN}(?:\s+at\s+\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?))?|{MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s+at\s+\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?))?)\b"
)
DATE_PHRASE_RES = (
    re.compile(r"(?i)\b(tomorrow(?:\s+(?:morning|afternoon|evening|night))?)\b"),
    re.compile(r"(?i)\b(today|tonight)\b"),
    re.compile(r"(?i)\b(next\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b"),
    re.compile(r"(?i)\b(this\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b"),
    re.compile(r"(?i)\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"),
    re.compile(r"(?i)\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"(?i)\b((?:on\s+)?(?:the\s+)?\d{1,2}(?:st|nd|rd|th))\b"),
    re.compile(rf"(?i)\b(\d{{1,2}}(?:st|nd|rd|th)?\s+of\s+{MONTH_PATTERN}(?:\s+at\s+\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?))?)\b"),
    re.compile(rf"(?i)\b({MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s+at\s+\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?))?)\b"),
)
EVENT_WORD_RE = re.compile(
    r"(?i)\b(birthday|anniversary|appointment|meeting|flight|shift|deadline|due|by\b|on\b)\b"
)
SCHEDULE_HINT_RE = re.compile(
    r"(?i)\b(due|date|time|tomorrow|tonight|morning|afternoon|evening|next|schedule|set the task|by\b|deadline)\b"
)
MUTATION_REQUEST_RE = re.compile(
    r"(?i)\b(add|create|make|schedule|set|reschedule|move|remove|delete|cancel)\b"
)
EVENT_INSPECTION_HINT_RE = re.compile(
    r"(?i)\b(check|calendar|scheduled|schedule|remember|remind|haven't done|have not done|didn't do|did not do|forgot|forget|what about|thing)\b"
)
GENERIC_EVENT_STAGE_RE = re.compile(
    r"(?i)\b(handle|ensure|check|confirm|inspect|review|look up|update the user's memory about|update memory about)\b"
)
COMPLETION_HINT_RE = re.compile(
    r"(?i)\b(done|finished|completed|handled|resolved|took care of|went to|attended|submitted|sent|bought|got|picked up|sorted|did it|did that|did this)\b"
)
CANCEL_HINT_RE = re.compile(
    r"(?i)\b(cancel|remove|delete|drop|skip|forget it|don't do|dont do|didn't do|didnt do|have not done|haven't done)\b"
)
CORRECTION_ONLY_HINT_RE = re.compile(
    r"(?i)\b(no\b|actually\b|off\b|day off\b|not working\b|no work\b|free tomorrow\b|free today\b|holiday\b|vacation\b)\b"
)
WORKLIKE_HINT_RE = re.compile(
    r"(?i)\b(work|shift|schedule|wake early|wake up early|day off|off tomorrow|off today)\b"
)
TASK_FOLLOWUP_HINT_RE = re.compile(
    r"(?i)\b(task|tasks|to-?do|pending queue|pending list|pending tasks|task list)\b"
)
REMINDER_REQUEST_RE = re.compile(
    r"(?i)\b(remind me to|remember to|set a reminder to|set reminder to|remind me about|set a reminder for|remind me that)\b"
)
SPECULATIVE_ACTION_RE = re.compile(
    r"(?is)^\s*(?:hmm[,.! ]+|well[,.! ]+)?(?:maybe|perhaps)\s+(?:i|we)\s+should\b"
    r"|^\s*(?:i|we)\s+might\s+(?:need|want|have)\s+to\b"
    r"|^\s*(?:should|could)\s+(?:i|we)\b"
)
EXPLICIT_ASSISTANT_REQUEST_RE = re.compile(
    r"(?i)\b(?:can|could|would|will)\s+you\b|\bplease\b|\bgo ahead and\b|\bhelp me\b|\bfor me\b"
)
KNOWLEDGE_STORE_RE = re.compile(
    r"(?is)^(?:please\s+)?remember(?:\s+that)?\s+(?!to\b)(?P<subject>.+?)\s+(?:is|are)\s+(?P<value>.+?)[.?!]*$"
)
KNOWLEDGE_REMOVE_RE = re.compile(
    r"(?is)^(?:please\s+)?(?:forget|remove|delete)\s+(?:that\s+)?(?P<body>.+?)[.?!]*$"
)
KNOWLEDGE_QUERY_RE = re.compile(
    r"(?is)^(?:so\s+)?(?:what do you know about|what do you remember about|do you remember|what is|what's|whats)\s+(?P<subject>.+?)(?:\s+now)?[?!.]*$"
)
READONLY_TASK_EVENT_QUERY_RE = re.compile(
    r"(?i)^(?:so\s+)?(?:what|which|show|list|tell me|do i have|what do i have|what's|whats)\b.*\b(task|tasks|to-?do|to-?dos|event|events|calendar|schedule)\b"
)
DIRECT_EVENT_ASSERTION_RE = re.compile(
    r"(?i)\b(i have|i've got|ive got|i got|i already got|i made|i booked|i scheduled|there is|there's)\b"
)
FILE_ORG_REQUEST_RE = re.compile(
    r"(?i)\b(organi[sz]e|reorgani[sz]e|clean up|tidy|merge|consolidat(?:e|ion)|group|sort)\b"
)
EXTENSION_GROUPING_RE = re.compile(
    r"(?i)\b(by extension|same extension|file extensions?|group(?:ing)? .* extension|extension folders?)\b"
)
FILE_TYPE_GROUPING_RE = re.compile(
    r"(?i)\b(file types?|png|jpg|jpeg|gif|webp|txt|json|py|photos?|images?|text files?|duplicate folders?|stray files?)\b"
)
EMPTY_DIR_CLEANUP_RE = re.compile(
    r"(?i)\b(delete|remove|clean up|clear)\s+empty\s+(?:folders|directories)\b|\bduplicate folders?\b"
)
_REL_PATH_TOKEN = r"[A-Za-z0-9_./\\-]+"
_FILE_PATH_TOKEN = rf"{_REL_PATH_TOKEN}\.[A-Za-z0-9]{{1,8}}"
DIRECT_FILE_CREATE_TEXT_RE = re.compile(
    rf"(?is)^(?:in the workspace,\s*)?(?:please\s+)?(?:create|write|make)(?:\s+the)?\s+(?:text\s+)?file\s+(?P<path>{_FILE_PATH_TOKEN})\s+with\s+(?:the\s+)?exact\s+contents?\s*:\s*(?P<content>.+?)\s*$"
)
DIRECT_FILE_COPY_RE = re.compile(
    rf"(?is)^(?:in the workspace,\s*)?(?:please\s+)?(?:create\s+the\s+folder\s+(?:{_REL_PATH_TOKEN})\s+if\s+needed\s+and\s+)?copy\s+(?P<src>{_FILE_PATH_TOKEN})\s+to\s+(?P<dst>{_FILE_PATH_TOKEN})[.?!]*$"
)
DIRECT_FILE_MOVE_RE = re.compile(
    rf"(?is)^(?:in the workspace,\s*)?(?:please\s+)?move\s+(?P<src>{_FILE_PATH_TOKEN})\s+to\s+(?P<dst>{_FILE_PATH_TOKEN})[.?!]*$"
)
DIRECT_FILE_READ_RE = re.compile(
    rf"(?is)^(?:in the workspace,\s*)?(?:please\s+)?read\s+the\s+file\s+(?P<path>{_FILE_PATH_TOKEN})\b.*$"
)
DIRECT_FILE_DELETE_RE = re.compile(
    rf"(?is)^(?:in the workspace,\s*)?(?:please\s+)?(?:delete|remove)\s+the\s+file\s+(?P<path>{_FILE_PATH_TOKEN})\b.*$"
)
DIRECT_FILE_REMOVE_TEXT_RE = re.compile(
    r'(?is)^(?:in the workspace,\s*)?(?:please\s+)?(?:remove|delete)\s+(?P<needle>"[^"]+"|\'[^\']+\'|.+?)\s+from\s+(?:the\s+)?(?P<subject>.+?)(?:[.?!]|$)'
)
DIRECT_FILE_REPLACE_TEXT_RE = re.compile(
    r'(?is)^(?:in the workspace,\s*)?(?:please\s+)?replace\s+(?P<old>"[^"]+"|\'[^\']+\')\s+with\s+(?P<new>"[^"]+"|\'[^\']+\')\s+in\s+(?:the\s+)?(?P<subject>.+?)(?:[.?!]|$)'
)
VAGUE_EVENT_FOLLOWUP_RE = re.compile(
    r"(?i)\b(what about|the thing|remember|forgot|forget|haven't done|have not done|didn't do|did not do|check)\b"
)
EXISTING_RECORD_HINT_RE = re.compile(
    r"(?i)\b("
    r"pending|already|existing|currently|still|previously|prior|active|logged|"
    r"task list|task queue|pending tasks|pending task|upcoming events|calendar|"
    r"scheduled|on the calendar|in the list|local log|reminder|previous request|"
    r"previously added|already added|was added|was scheduled|has been scheduled"
    r")\b"
)
SUBJECT_HINT_PATTERNS = (
    re.compile(r"(?i)^(.+?)\s+is\s+noted\s+for\b"),
    re.compile(r"(?i)^(.+?)\s+is\s+(?:on\s+)?(?:today|tonight|tomorrow|next\b|this\b|\d{4}-\d{2}-\d{2})"),
    re.compile(r"(?i)^(.+?)\s+was\s+previously\s+set\s+for\b"),
    re.compile(r"(?i)\breferring to (?:the\s+)?(.+)$"),
    re.compile(r"(?i)^check (?:the user'?s\s+)?calendar for (.+)$"),
    re.compile(r"(?i)^check upcoming events for ['\"]?(.+?)['\"]?$"),
    re.compile(r"(?i)^handle (.+?) event$"),
    re.compile(r"(?i)^ensure (.+?) event(?: is .+)?$"),
    re.compile(r"(?i)^update (?:the user'?s\s+)?memory about (.+)$"),
)
