"""Hebrew user-interface messages."""

MESSAGES = {
    "status.thinking": "המודל חושב...",
    "status.async_ack": "הבקשה התקבלה ונכנסה לתור.\nמזהה משימה: {task_id}",
    "error.request_failed": "הבקשה נכשלה: {reason}",
    "debug.llm_call": (
        "קריאת LLM אל {provider}/{model} הסתיימה בתוך {latency_ms} מילישניות; "
        "מספר tokens: {tokens}."
    ),
    "debug.api_received": "מעקב: הבקשה הגיעה ל-API ולסוכן הראשי.",
    "debug.intent": "מעקב: הסוכן הראשי סיווג את ההודעה כ-{intent}.",
    "debug.report": "מעקב: הדיווח {event_id} נשמר ונכנס לתור.",
    "debug.request": "מעקב: בקשת הפעולה {event_id} נשמרה ונכנסה לתור.",
    "debug.extraction": "מעקב: נתוני האירוע זוהו כסיווג={classification}, אזור={area}.",
    "debug.risk": "מעקב: הערכת הסיכון החזירה {risk_level}.",
    "debug.protocol": "מעקב: סטטוס בחירת הפרוטוקול={status}, פרוטוקול={protocol}.",
    "debug.hold_created": "מעקב: נוצרה המתנה מסוג {hold_kind} עבור האירוע {event_id}.",
    "debug.hold_resolved": "מעקב: ההמתנה מסוג {hold_kind} נפתרה עבור האירוע {event_id}.",
    "debug.queue": "מעקב: העבודה מהתור התחילה לאחר {wait_ms} מילישניות.",
    "debug.stage": "מעקב: השלב {stage} הסתיים בסטטוס {status} בתוך {latency_ms} מילישניות.",
    "debug.step_start": "מעקב: צעד {step_index} הועבר אל {agent}.",
    "debug.step_result": "מעקב: צעד {step_index} של {agent} הסתיים בסטטוס {status}.",
    "debug.step_retry": "מעקב: הצעד של {agent} מופעל שוב (ניסיון {attempt}).",
    "debug.step_failed": "מעקב: הצעד של {agent} נכשל בניסיון {attempt}.",
    "debug.waiting_data": "מעקב: עבודת הפרוטוקול ממתינה לשדות האירוע: {fields}.",
    "debug.tool": "מעקב: הכלי {tool} של {agent} הסתיים בסטטוס {status}.",
    "debug.tool_blocked": "מעקב: נחסמה הפעלת הכלי הלא מורשה {tool} עבור {agent}.",
    "debug.provider": "מעקב: ספק LLM={provider}, מודל={model} הסתיים בתוך {latency_ms} מילישניות; טוקנים: {tokens}.",
    "debug.provider_failed": "מעקב: ספק LLM={provider}, מודל={model} נכשל לאחר {latency_ms} מילישניות; טוקנים: {tokens}.",
    "debug.insight": "מעקב: סוכן התובנות החזיר הערכה עבור {protocol}.",
    "debug.judgment": "מעקב: תהליך השפיטה החזיר פסק {verdict}.",
    "debug.outcome": "מעקב: האירוע {event_id} הגיע לתוצאה {outcome}.",
    "debug.queue_failed": "מעקב: העבודה מהתור נכשלה.",
    "debug.tokens_breakdown": "קלט={input}, פלט={output}, מטמון={cache}",
    "debug.tokens_unavailable": "לא זמין",
    "header.clarification_needed": "[נדרשת הבהרה — נא להשיב]",
    "header.approval_needed": "[נדרש אישור — נא להשיב]",
    "header.precedent_closure": "[הודעה — נסגר על סמך תקדים — אין צורך להשיב]",
    "header.uncertain_verdict": "[הודעה — תוצאה לא ודאית — אין צורך להשיב]",
    "header.no_match": "[הודעה — אין פרוטוקול מתאים — אין צורך להשיב]",
    "header.result": "[תוצאה]",
    "header.failed": "[הריצה נכשלה]",
    "header.declined": "[נדחה]",
    "header.event_data_needed": "[נדרשים פרטים נוספים על האירוע]",
    "result.verdict": "תוצאה: {outcome}",
    "result.what_was_done": "מה בוצע:",
    "result.insight": "תובנה:",
    "failure.failed_step": "השלב שנכשל: {agent}",
    "failure.reason": "סיבה: {reason}",
    "failure.completed_before": "הושלם לפני הכשל:",
    "failure.nothing_completed": "לא הושלם דבר לפני הכשל.",
    "common.unknown": "(לא ידוע)",
    "common.none": "(אין)",
    "common.no_reason": "(לא נמסרה סיבה)",
    "auth.unregistered": (
        "אינך משתמש רשום במערכת זו (זהות: {identity}). "
        "מנהל מערכת חייב להוסיף אותך לפני שתוכל להשתמש בבוט."
    ),
    "auth.operation_refused": (
        "הפעולה '{operation}' דורשת הרשאת מפקד; החשבון שלך "
        "({identity}) רשום ברמת {level}."
    ),
    "profile.nothing_changed": (
        "המערכת הפעילה לא השתנתה — העריכה תיכנס לתוקף בהפעלה הבאה."
    ),
    "profile.name": "פרופיל: {profile_name}",
    "profile.agents": "סוכנים:",
    "profile.protocols": "פרוטוקולים:",
    "profile.protocol_requires_approval": "דורש אישור",
    "profile.protocol_no_approval": "אינו דורש אישור",
    "profile.protocol_line": (
        "- {name} (רמת קריטיות: {criticality}, {approval}): {description}"
    ),
    "profile.event_types": "סוגי אירועים: {event_types}",
    "profile.areas": "אזורים: {areas}",
    "profile.restart_pending": (
        "קובץ הפרופיל בדיסק שונה מהגרסה הפעילה. נדרשת הפעלה מחדש כדי להחיל את השינוי."
    ),
    "profile.restart_not_pending": (
        "קובץ הפרופיל בדיסק תואם לגרסה הפעילה. אין צורך בהפעלה מחדש."
    ),
    "protocol.approval_flag_required": (
        "חובה להגדיר את 'approval_flag' במפורש כ-true או false; אין לו ערך ברירת מחדל."
    ),
    "common.rejected": "נדחה: {message}",
    "settings.view": (
        "מספר ניסיונות: {retry_count}\nסף סיכון: {risk_threshold}\n"
        "חלון היסטוריה (ימים): {lookback_window_days}"
    ),
    "settings.retry_whole": "הערך 'retry_count' חייב להיות מספר שלם, התקבל {value}.",
    "settings.retry_nonnegative": "הערך 'retry_count' אינו יכול להיות שלילי.",
    "settings.risk_number": "הערך 'risk_threshold' חייב להיות מספר, התקבל {value}.",
    "settings.risk_range": "הערך 'risk_threshold' חייב להיות בין 0.0 ל-1.0.",
    "settings.lookback_whole": (
        "הערך 'lookback_window_days' חייב להיות מספר שלם, התקבל {value}."
    ),
    "settings.lookback_positive": (
        "הערך 'lookback_window_days' חייב להיות לפחות 1; חלון באורך אפס אינו תקין."
    ),
    "settings.unknown": (
        "הגדרה לא מוכרת: {field}. ניתן לשנות רק retry_count, risk_threshold "
        "ו-lookback_window_days."
    ),
    "settings.saved": (
        "{message}\n\nהשינוי נכנס לתוקף מיד ונשמר; בניגוד לעריכת פרופיל, אין צורך בהפעלה מחדש."
    ),
    "approval.risk": "סיכון: {risk_level} ({risk_reason})",
    "approval.flagged": (
        "{header}\n\nפרוטוקול שממתין לאישור: {protocol_name}\n{risk}\n\nהאם להפעיל אותו?"
    ),
    "approval.ambiguous": (
        "{header}\n\nכמה פרוטוקולים מתאימים באותה מידה:\n{candidates}\n{risk}\n\nאיזה מהם להפעיל?"
    ),
    "approval.approve": "אישור",
    "approval.reject": "דחייה",
    "approval.resumed": "האישור נשמר והפרוטוקול חודש.",
    "approval.rejected": "הדחייה נשמרה; האירוע לא יופעל.",
    "approval.already_answered": "האישור כבר נענה{who}. {message}",
    "clarification.prompt": (
        "{header}\n\nהדיווח המקורי:\n{raw_text}\n\nלא ניתן היה לזהות: {field}.\n"
        "יש לבחור את הסיווג הנכון להלן."
    ),
    "clarification.resumed": "הבחירה נשמרה והתהליך חודש.",
    "clarification.already_resolved": "ההבהרה כבר נפתרה{who}. {message}",
    "common.by_identity": " על ידי {identity}",
    "notice.uncertain": (
        "{header}\n\nאירוע {event_id} הסתיים בתוצאה לא ודאית.\n\nתובנה:\n{insight}"
    ),
    "notice.no_match": (
        "{header}\n\nאין פרוטוקול קיים שיכול למלא בקשה זו.\nטקסט מקורי: {raw_text}\n"
        "{reason}\nסיכון: {risk_level} ({risk_reason})"
    ),
    "notice.precedent": (
        "{header}\n\nאירוע: {raw_text}\n\nנסגר מול תקדים {precedent_id}, "
        "שהסתיים כך: {ending}"
    ),
    "bot.not_available": "האפשרות עדיין אינה זמינה: {reason}",
    "bot.handler_error": "אירעה שגיאה בטיפול בבקשה. פרטי השגיאה נרשמו.",
    "bot.no_answer": "(לא הוחזרה תשובה)",
    "bot.refused": "הבקשה נדחתה: {message}",
    "bot.taken_as": "הבקשה התקבלה וסווגה כ-{kind}.",
    "bot.waiting_approval": "הבקשה ממתינה כעת לאישור מפקד.",
    "bot.job_queued": "מזהה משימה: {job_id}. התוצאה תישלח כאן בסיום.",
    "protocol.expected_fields": (
        "נדרשים 7 שדות המופרדים בקו אנכי — name | description | "
        "participating_agents (מופרדים בפסיקים) | approved_tools (מופרדים בפסיקים) | "
        "expected_success_output | criticality | approval_flag (true/false)."
    ),
    "protocol.flag_boolean": "הערך 'approval_flag' חייב להיות בדיוק 'true' או 'false'.",
    "command.profile_usage": "שימוש: /profile view | diff | add ... | edit ... | remove <name>",
    "command.settings_usage": (
        "שימוש: /settings view | set <retry_count|risk_threshold|lookback_window_days> <value>"
    ),
    "api.internal_error": "אירעה שגיאה פנימית.",
    "api.identity_required": "לא סופקה זהות משתמש.",
    "api.identity_unregistered": "הזהות '{identity}' אינה רשומה במערכת.",
    "api.operation_forbidden": "רמת ההרשאה {level} אינה רשאית לבצע {operation}.",
    "api.field_required": "השדה '{field}' הוא שדה חובה.",
    "api.conversation_id_invalid": (
        "השדה 'conversation_id' חייב להיות מחרוזת לא ריקה באורך של עד 200 תווים."
    ),
    "api.queue_full": "תור האירועים מלא; יש לנסות שוב מאוחר יותר.",
    "api.queue_full_event_detail": "תור האירועים מלא; יש לנסות לשלוח את פרטי האירוע מאוחר יותר.",
    "api.event_detail_again": "נא לספק שוב את פרטי האירוע החסרים.",
    "api.clarify_check_record_do": "נא להבהיר מה ברצונך שאבדוק, אתעד או אבצע.",
    "api.clarify_action": "נא להבהיר מה ברצונך שאבצע.",
    "api.queued_report": "הדיווח נכנס לתור. מזהה משימה: {task_id}.",
    "api.queued_request": "הבקשה נכנסה לתור. מזהה משימה: {task_id}.",
    "api.missing_required_field": "חסר שדה חובה: {field}.",
    "api.malformed_protocol": "מבנה הפרוטוקול אינו תקין: {reason}",
    "api.profile_field_restart": (
        "השדה '{field}' שייך לפרופיל וייכנס לתוקף רק לאחר הפעלה מחדש."
    ),
    "api.retry_nonnegative_integer": "הערך 'retry_count' חייב להיות מספר שלם שאינו שלילי.",
    "api.risk_threshold_range": "הערך 'risk_threshold' חייב להיות מספר בין 0.0 ל-1.0.",
    "api.lookback_positive_integer": "הערך 'lookback_window_days' חייב להיות מספר שלם חיובי.",
    "api.other_identity_forbidden": "צופה אינו רשאי לצפות ברישום של זהות אחרת.",
    "api.job_not_found": "לא נמצאה משימה עם המזהה '{task_id}'.",
    "api.hold_not_found": "לא נוצרה המתנת {kind} עבור האירוע '{event_id}'.",
    "api.hold_resolved": "כבר נפתר על ידי '{identity}' בזמן {resolved_at}.",
    "api.decision_required": (
        "השדה 'decision' הוא חובה: 'approved', 'rejected' או שם פרוטוקול מועמד."
    ),
    "api.cursor_invalid": "השדה 'since' חייב להיות סמן שלם שאינו שלילי.",
    "api.wait_invalid": "השדה 'wait_seconds' חייב להיות מספר שלם בין 0 ל-30.",
    "api.trace_id_invalid": "מזהה המעקב אינו תקין.",
    "api.deep_debug_disabled": "מצב Deep Debug אינו מופעל בשרת הזה.",
    "terminal.mode_prompt": "\nמצב — [m] הודעה, [e] אירוע, או [q] יציאה? ",
    "terminal.mode_invalid": "יש להקליד 'm', 'e' או 'q'.",
    "terminal.sample_events": "\nאירועי חיישן לדוגמה:",
    "terminal.sample_fire": "דיווח אש — הגזרה הצפונית",
    "terminal.sample_medical": "דיווח רפואי — הגזרה הדרומית",
    "terminal.sample_unknown": "קריאה בלתי ניתנת לסיווג (יוצרת בקשת הבהרה)",
    "terminal.sample_custom": "מותאם אישית — הקלדת טקסט חופשי",
    "terminal.back": "  [q] חזרה לבחירת מצב",
    "terminal.choose_prompt": "בחירה> ",
    "terminal.invalid_choice": "בחירה לא תקינה.",
    "terminal.event_text": "טקסט האירוע> ",
    "terminal.event_text_default": "טקסט האירוע [{default}]> ",
    "terminal.sender_default": "זהות מדווח [{default}]> ",
    "terminal.message_prompt": "\nהודעה> ",
    "terminal.request_failed": "(הבקשה נכשלה: {reason})",
    "terminal.submission_refused": "השליחה נדחתה ({status}): {reason}",
    "terminal.submitted": "נשלח: event_id={event_id} status={status}",
    "terminal.waiting": "\n(ממתין לתוצאה — Ctrl+C לעצירה וחזרה למסך ההודעות)",
    "terminal.poll_failed": "(בדיקת ההתראות נכשלה: {reason}; מנסה שוב)",
    "terminal.profile": "פרופיל:  {profile}",
    "terminal.database": "מסד נתונים: {database}",
    "terminal.api": "API:      {base_url}  (יש לוודא שהפקודה `{command}` כבר פועלת)",
    "terminal.background": (
        "(בדיקת התראות ברקע מתחילה מיד; ניתן להקליד /holds בכל עת במסך ההודעות)"
    ),
    "terminal.goodbye": "\nלהתראות.",
    "terminal.skip_existing": (
        "(מדלג על {count} התראות קיימות מהיסטוריית deployment זה, שנוצרו לפני הפעלה זו)"
    ),
    "terminal.first_run_skip": (
        "(הפעלה ראשונה עבור הזהות {identity} — מדלג על {count} התראות קיימות; "
        "בהפעלות הבאות הסמן ימשיך מנקודה זו בדומה לבוט האמיתי)"
    ),
    "terminal.poll_background_error": "(בדיקת ההתראות ברקע נכשלה ומנסה שוב: {reason})",
    "terminal.new_notifications": "--- {count} התראות חדשות מאז הפעולה האחרונה ---",
    "terminal.holds_need_answer": "({count} מהן דורשות תשובה — יש להקליד /holds לבדיקה)",
    "terminal.clarification_hold": "בקשת הבהרה — אירוע {event_id}",
    "terminal.choose_classification": "יש לבחור את הסיווג הנכון:",
    "terminal.skip_hold": "  [s] דילוג זמני (השארת הבקשה פתוחה)",
    "terminal.skipped_hold": "(דולג — הבקשה נשארה פתוחה; ניתן לחזור אליה באמצעות /holds)",
    "terminal.your_choice": "הבחירה שלך> ",
    "terminal.invalid_hold_choice": "בחירה לא תקינה — יש לבחור מספר מהרשימה או 's' לדילוג.",
    "terminal.choose": "בחירה:",
    "terminal.no_holds": "אין בקשות ממתינות כרגע.",
    "terminal.identity_exists": "זהות ברמת {level} כבר קיימת: {identity}.",
    "terminal.provision_identity": "מגדיר זהות ברמת {level} באמצעות `cli.user_admin`: {identity}",
    "terminal.provision_service": (
        "מגדיר את זהות השירות של הבוט באמצעות `cli.user_admin`: {identity}"
    ),
}
