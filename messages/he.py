"""Hebrew user-interface messages."""

MESSAGES = {
    "status.thinking": "המודל חושב...",
    "status.async_ack": "הבקשה התקבלה ונכנסה לתור.\nמזהה משימה: {task_id}\nהתוצאה תישלח כאן בסיום.",
    "error.request_failed": "הבקשה נכשלה: {reason}",
    "error.run_failure_generic": "לא הצלחתי לעבד את זה — נסה לנסח מחדש או פנה למפקד.",
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
    "header.uncertain_reporter": "[עדכון]",
    "header.no_match": "[הודעה — אין פרוטוקול מתאים — אין צורך להשיב]",
    "header.result": "[תוצאה]",
    "header.failed": "[הריצה נכשלה]",
    "header.declined": "[נדחה]",
    "header.event_data_needed": "[נדרשים פרטים נוספים על האירוע]",
    "result.verdict": "תוצאה: {outcome}",
    "result.job_id": "מזהה משימה: {job_id}",
    "outcome.succeeded": "הצליח",
    "outcome.failed": "נכשל",
    "outcome.uncertain": "לא ודאי",
    "outcome.closed_on_precedent": "נסגר על סמך תקדים",
    "outcome.declined": "נדחה",
    "outcome.no_match_protocol": "לא נמצא פרוטוקול מתאים",
    "risk.high": "גבוה",
    "risk.low": "נמוך",
    "result.what_was_done": "מה בוצע:",
    "result.insight": "תובנה:",
    "result.protocol_suffix": "פרוטוקול: {protocol_name} ({risk_level}, {reason})",
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
    "notice.uncertain_reporter": (
        "{header}\n\nהאירוע שדיווחת עליו עדיין נבדק.\nנעדכן אותך כשיהיה מידע נוסף."
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
    "bot.welcome": (
        "שלום — זהו {profile_name}. דווח על משהו, שאל שאלה, "
        "או בקש פעולה — פשוט הקלד."
    ),
    "bot.full_name_prompt": "לפני שנמשיך, נא לכתוב שם מלא (לפחות שתי מילים).",
    "bot.full_name_invalid": "השם אינו ברור. נא לכתוב שם מלא הכולל לפחות שתי מילים.",
    "bot.full_name_saved": "תודה, {name}. השם נשמר וכעת נמשיך בבקשה הקודמת.",
    "command.menu_start": "התחלה",
    "command.menu_profile": "צפייה בפרופיל הפעיל או עריכתו",
    "command.menu_settings": "צפייה בהגדרות חיות או שינויין",
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
    "api.sender_identity_mismatch": "זהות המדווח אינה תואמת לזהות המשתמש המאומתת.",
    "api.event_data_event_id_invalid": "מזהה אירוע ההמשך אינו תקין.",
    "api.event_data_reply_not_pending": "בקשת ההשלמה אינה פתוחה עבור משתמש ושיחה אלה.",
    "api.identity_unregistered": "הזהות '{identity}' אינה רשומה במערכת.",
    "api.operation_forbidden": "רמת ההרשאה {level} אינה רשאית לבצע {operation}.",
    "api.field_required": "השדה '{field}' הוא שדה חובה.",
    "api.conversation_id_invalid": (
        "השדה 'conversation_id' חייב להיות מחרוזת לא ריקה באורך של עד 200 תווים."
    ),
    "api.queue_full": "תור האירועים מלא; יש לנסות שוב מאוחר יותר.",
    "api.queue_full_event_detail": "תור האירועים מלא; יש לנסות לשלוח את פרטי האירוע מאוחר יותר.",
    "api.event_detail_again": "נא לספק שוב את פרטי האירוע החסרים.",
    "api.event_detail_ambiguous": (
        "יש כרגע {count} דיווחים הממתינים לפרטים חסרים, ולכן אינני יכול לדעת "
        "לאיזה מהם מתייחסת התגובה הזו. על מפקד לסגור קודם את הישן מביניהם, "
        "ולאחר מכן ניתן להשיב שוב."
    ),
    "api.clarify_check_record_do": "נא להבהיר מה ברצונך שאבדוק, אתעד או אבצע.",
    "api.clarify_action": "נא להבהיר מה ברצונך שאבצע.",
    "api.drone_selection_invalid": "לא זיהיתי רחפן מתאים. בחר שם או מזהה מהרשימה:\n{choices}\nאפשר גם לכתוב: כולם",
    "api.drone_recall_none": "אין כרגע רחפנים במשימה; לא בוצע שינוי.",
    "api.drone_recall_all_done": "הוחזרו לבסיס: {names}. המשימות נסגרו.",
    "api.drone_recall_one_done": "{callsign} הוחזר לבסיס. המשימה {mission_id} נסגרה.",
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
    "api.full_name_invalid": "השדה 'full_name' חייב לכלול לפחות שתי מילים ולהיות באורך של עד 120 תווים.",
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
    "api.group_not_registered": "קבוצת הטלגרם '{chat_id}' אינה רשומה לניתוב.",
    "api.protocol_out_of_group_scope": "הפרוטוקול '{protocol}' אינו זמין בקבוצה זו (מנותבת אל {agent}).",
    "api.group_agent_invalid": "'{agent}' אינו סוכן שניתן לנתב אליו. מותר: {allowed}.",
    "api.attendance_agent_unavailable": "לא רשום סוכן נוכחות בפריסה הזו.",
    "bot.group_added_hint": (
        "הקבוצה הזו (מזהה צ'אט {chat_id}) עדיין לא רשומה. מפקד צריך לשייך אותה לסוכן "
        "בממשק הניהול או בפקודת ניהול הקבוצות לפני שהודעות כאן יטופלו."
    ),
    "bot.unavailability_prompt_group": "{name}, אנא השב להודעה זו עם סיבת אי-הזמינות ומספר ימים משוער (לדוגמה: 'עקב מחלה ליומיים').",
    "attendance.group_prompt": (
        "בדיקת נוכחות יומית לכיתת הכוננות. יש להשיב תוך שעה (עד {deadline}) על זמינותך. "
        "אם אינך זמין, ציין סיבה ומספר ימים.\n\nחברים שנדרשים לדווח:\n{members}"
    ),
    "attendance.group_prompt_nobody": "בדיקת הנוכחות היומית נפתחה. אין חברים שנדרשים לדווח היום.",
    "attendance.button_available": "אני זמין לכוננות",
    "attendance.button_unavailable": "איני זמין",
    "attendance.already_open": "מחזור הנוכחות של היום כבר פתוח.",
    "bot.unavailability_prompt": "אנא ציין את סיבת אי-הזמינות ומספר ימים משוער (לדוגמה: 'עקב מחלה ליומיים').",
    "bot.unavailability_days_prompt": "הסיבה נשמרה. לכמה ימים אינך זמין? אנא שלח מספר ימים, לדוגמה: 2.",
    "bot.availability_report_available": "דיווח נוכחות כיתת כוננות: המשתמש {identity} זמין לכוננות.",
    "bot.availability_report_unavailable": "דיווח כוננות: המשתמש {identity} אינו זמין. סיבה: {reason}. משך אי-הזמינות: {days} ימים. אנא עדכן את מצב הזמינות בהתאם.",
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
    "admin.login_wrong_credentials": "שם משתמש או סיסמה שגויים.",
    "admin.login_locked_out": "יותר מדי ניסיונות כושלים. נסה שוב בעוד {duration}.",
    "admin.lockout_less_than_a_minute": "פחות מדקה",
    "admin.lockout_one_minute": "כדקה אחת",
    "admin.lockout_minutes": "כ-{minutes} דקות",
    "admin.lockout_one_hour": "כשעה אחת",
    "admin.lockout_hours": "כ-{hours} שעות",
    "admin.login_title": "כניסת מנהל",
    "admin.login_subtitle": "לוח הבקרה של הבוט",
    "admin.username": "שם משתמש",
    "admin.password": "סיסמה",
    "admin.sign_in": "כניסה",
    "admin.connected": "מחובר",
    "admin.log_out": "יציאה",
    "admin.dashboard_title": "ניהול משתמשים",
    "admin.dashboard_subtitle": "ניהול מי רשאי לדבר עם הבוט ומה מותר לו לעשות.",
    "admin.menu_title": "ניהול המערכת",
    "admin.menu_subtitle": "בחר את האזור שברצונך לנהל.",
    "admin.menu_profiles": "פרופילים",
    "admin.menu_protocols": "פרוטוקולים",
    "admin.menu_events": "אירועים",
    "admin.menu_users": "ניהול משתמשים",
    "admin.menu_groups": "ניהול קבוצות",
    "admin.menu_simulator": "סימולציות",
    "admin.menu_server": "ניהול שרת",
    "admin.server_title": "ניהול שרת",
    "admin.server_subtitle": "איפוס הסביבה הפעילה או הפעלה מחדש עם פרופיל מקומי אחר.",
    "admin.server_profile": "פרופיל הפעלה",
    "admin.server_active_profile": "הפרופיל הפעיל: {profile}",
    "admin.server_load_profile": "טען והפעל מחדש",
    "admin.server_restart_required": "טעינת פרופיל דורשת הפעלה מחדש אוטומטית של ה־API ושל הבוט.",
    "admin.server_reset": "איפוס מסדי נתונים",
    "admin.server_reset_help": "הפעולה מוחקת את כל מסדי הנתונים שהפרופיל הצהיר עליהם ומפעילה מחדש מסדים ריקים. יהיה צורך לרשום מחדש משתמשי Telegram וקבוצות.",
    "admin.server_reset_confirm": "למחוק את כל מסדי הנתונים של הפרופיל ולהפעיל מחדש? לא ניתן לבטל פעולה זו.",
    "admin.server_reset_button": "אפס והפעל מחדש",
    "admin.server_unavailable": "הפעולה זמינה רק כאשר המערכת הופעלה באמצעות run_stack.py.",
    "admin.server_profile_invalid": "הפרופיל שנבחר אינו פרופיל מקומי זמין.",
    "admin.server_reset_confirmation_missing": "נדרש אישור מפורש לאיפוס.",
    "admin.server_restarting": "השרת מופעל מחדש",
    "admin.server_restarting_help": "ה־API והבוט מופעלים מחדש. הדף יתחבר שוב באופן אוטומטי.",
    "admin.server_retry_link": "נסה להתחבר עכשיו",
    "admin.nav_menu": "תפריט ניהול",
    "admin.users_title": "ניהול משתמשים",
    "admin.users_subtitle": "ניהול מזהי טלגרם, שמות ורמות הרשאה.",
    "admin.nav_dashboard": "ניהול משתמשים",
    "admin.nav_simulator": "סימולטור תרחישים",
    "admin.api.identity_title": "זהות לביצוע קריאות API",
    "admin.api.identity_label": "זהות Telegram רשומה",
    "admin.api.missing_name": "שם חסר",
    "admin.api.identity_save": "השתמש בזהות הזאת",
    "admin.api.no_identity": "יש לרשום משתמש Telegram אנושי לפני ביצוע קריאות API מאומתות.",
    "admin.api.identity_help": "הקריאות נשלחות ל־endpoint הציבורי החי עם X-Identity זהה לבחירה. ההרשאות והשגיאות הרגילות נשארות בתוקף.",
    "admin.api.identity_invalid": "זהות ה־API שנבחרה אינה משתמש אנושי רשום.",
    "admin.api.identity_selected": "קריאות ה־API יבוצעו באמצעות הזהות {identity}.",
    "admin.api.execute": "בצע קריאה",
    "admin.api.sending": "שולח בקשה...",
    "admin.api.not_run": "הקריאה טרם הופעלה.",
    "admin.api.network_error": "שגיאת רשת",
    "admin.profiles.title": "פרופילים",
    "admin.profiles.subtitle": "צפייה בפרופיל הפעיל ושינוי ההגדרות החיות שלו באמצעות API של SYSTEM.",
    "admin.profiles.get_help": "מחזיר את סקירת הפרופיל ואת השדות המותרים לזהות שנבחרה.",
    "admin.profiles.put_help": "רק שדות שאינם ריקים יישלחו. שדות בקובץ הפרופיל עדיין דורשים הפעלה מחדש.",
    "admin.protocols.title": "פרוטוקולים",
    "admin.protocols.subtitle": "הצגה ועריכה של פרוטוקולי הפרופיל באמצעות API החי של Protocol.",
    "admin.protocols.list_help": "מציג את הפרוטוקולים הזמינים לזהות שנבחרה בתהליך הפעיל.",
    "admin.protocols.name": "שם הפרוטוקול",
    "admin.protocols.criticality": "רמת קריטיות",
    "admin.protocols.description": "תיאור",
    "admin.protocols.agents": "סוכנים משתתפים (מופרדים בפסיקים)",
    "admin.protocols.tools": "כלים מאושרים (מופרדים בפסיקים)",
    "admin.protocols.success": "פלט הצלחה צפוי",
    "admin.protocols.approval": "דורש אישור",
    "admin.protocols.delete_confirm": "למחוק את הפרוטוקול מקובץ הפרופיל? התהליך הפעיל ישתנה רק לאחר הפעלה מחדש.",
    "admin.events.title": "אירועים",
    "admin.events.subtitle": "שליחת הודעות ואירועי חיישן, צפייה במשימות וב־trace, וטיפול בעצירות תפעוליות באמצעות ה־API החי.",
    "admin.events.text": "טקסט",
    "admin.events.holds_help": "מציג עצירות לא פתורות הממתינות לאישור או לסיווג.",
    "admin.events.approval_help": "לעצירת אישור יש להזין approved/rejected; לעצירת בחירה לא חד־משמעית ניתן להזין שם פרוטוקול.",
    "admin.users.api_title": "פעולות API למשתמשים",
    "admin.users.self_name_help": "endpoint השם מאפשר לזהות לעדכן רק את השם של עצמה.",
    "admin.users.commanders_help": "מחזיר את רשימת המפקדים הגלויה לזהות שנבחרה.",
    "admin.groups.api_title": "פעולות API לקבוצות",
    "admin.groups.delete_confirm": "להסיר את הקבוצה באמצעות API החי של Groups?",
    "admin.col_identity": "זהות טלגרם",
    "admin.col_full_name": "שם מלא",
    "admin.col_level": "הרשאה",
    "admin.save": "שמור",
    "admin.remove": "הסר",
    "admin.add": "הוסף",
    "admin.tag_bot_service": "זהות השירות של הבוט עצמו",
    "admin.add_user": "הוספת משתמש",
    "admin.confirm_remove_user": "להסיר את {identity}?",
    "admin.groups_title": "קבוצות טלגרם",
    "admin.groups_page_subtitle": "קישור מזהי קבוצות טלגרם אמיתיים לתחום סוכן.",
    "admin.groups_subtitle": "קשר קבוצה לתת-הסוכן שההודעות שלה שייכות לו. קבוצות שאינן ברשימה - הבוט מתעלם מהן.",
    "admin.col_chat_id": "מזהה צ'אט",
    "admin.col_label": "תיאור",
    "admin.col_routed_to": "מנותב אל",
    "admin.no_groups": "עדיין לא נרשמו קבוצות.",
    "admin.confirm_remove_group": "להסיר את הקבוצה {chat_id}?",
    "admin.add_group": "הוספת קבוצה",
    "admin.add_group_help": (
        "מזהה הצ'אט הוא המספר השלילי שהבוט מפרסם כשמוסיפים אותו לקבוצה לא רשומה. "
        "בחר {main_agent} לניתוב מלא, או תת-סוכן אחד כדי להגביל את הקבוצה לפרוטוקולים שלו בלבד."
    ),
    "admin.label_placeholder": "כיתת כוננות",
    "admin.bot_service_title": "זהות השירות של הבוט",
    "admin.bot_service_help": (
        "רושם או רושם מחדש את {identity} ברמת מפקד. נדרש לפני שהבוט יכול לשאוב התראות, "
        "לקרוא את רשימת המפקדים או לבדוק שינויים בפרופיל."
    ),
    "admin.bot_service_button": "רישום bot-service",
    "admin.session_expired": "ההתחברות פקעה עקב חוסר פעילות - יש להיכנס שוב.",
    "admin.csrf_failed": "לא ניתן היה לאמת את הפעולה - נסה שוב.",
    "admin.signed_out": "יצאת מהמערכת.",
    "admin.chat_id_required": "נדרש מזהה צ'אט של טלגרם.",
    "admin.group_agent_invalid": "'{agent}' אינו סוכן שניתן לנתב אליו.",
    "admin.group_routed": "הקבוצה '{chat_id}' מנותבת כעת אל '{agent}'.",
    "admin.group_not_found": "אין קבוצה כזו: '{chat_id}'.",
    "admin.group_removed": "הקבוצה '{chat_id}' הוסרה.",
    "admin.identity_required": "נדרשת זהות טלגרם.",
    "admin.full_name_invalid": "שם מלא יכול להיות ריק או לכלול לפחות שתי מילים, ועד 120 תווים.",
    "admin.level_invalid": "'{level}' אינה רמת הרשאה תקינה.",
    "admin.user_written": "'{identity}' הוא כעת '{level}'.",
    "admin.user_not_found": "אין משתמש כזה: '{identity}'.",
    "admin.user_removed": "'{identity}' הוסר.",
    "admin.bot_service_provisioned": "'{identity}' רשום ברמת מפקד.",
    "admin.error_title": "משהו השתבש",
    "admin.error_subtitle": "נסה שוב, או בדוק את יומן השרת.",
    "admin.simulator.title": "סימולטור תרחישים",
    "admin.simulator.subtitle": "טען תרחיש והזרם את הצעדים שלו למערכת בדיוק כפי שהבוט היה עושה, צ'אט אחר צ'אט. כל צעד נשלח ל-API החי תחת זהות השולח שלו.",
    "admin.simulator.drop_zone": "לחץ לבחירת קובץ JSON של תרחיש, או גרור אותו לכאן",
    "admin.simulator.paste_label": "או הדבק JSON של תרחיש",
    "admin.simulator.load_pasted": "טען את ה-JSON שהודבק",
    "admin.simulator.examples": "דוגמאות מובנות",
    "admin.simulator.choose_example": "בחר אחת משש הדוגמאות",
    "admin.simulator.mapping_title": "מיפוי מזהי Telegram אמיתיים",
    "admin.simulator.mapping_help": "יש למפות כל דמות וקבוצה עבור הטעינה הזאת. המערכת מציעה ערכים הרשומים בשרת, אך המזהים שיוזנו יישלחו כפי שהם ל־API האמיתי ולא יישמרו לטעינה הבאה.",
    "admin.simulator.apply_mapping": "טען עם המיפוי הזה",
    "admin.simulator.example_invalid": "הדוגמה המובנית שנבחרה אינה זמינה.",
    "admin.simulator.telegram_id_placeholder": "Telegram ID חיובי",
    "admin.simulator.chat_id_placeholder": "chat ID שלילי לקבוצה",
    "admin.simulator.map_person": "{persona} — Telegram ID",
    "admin.simulator.map_group": "{group} — chat ID של הקבוצה",
    "admin.simulator.err_positive_identity": "יש למפות את {persona} ל־Telegram ID חיובי.",
    "admin.simulator.err_negative_group": "יש למפות את {group} ל־chat ID שלילי.",
    "admin.simulator.unregistered_name": "לא רשום",
    "admin.simulator.missing_name": "שם חסר",
    "admin.simulator.send_next": "שלח את הצעד הבא",
    "admin.simulator.reset_view": "נקה תצוגה",
    "admin.simulator.no_scenario": "לא נטען תרחיש",
    "admin.simulator.untitled": "תרחיש ללא כותרת",
    "admin.simulator.badge_id": "מזהה: {id}",
    "admin.simulator.badge_steps": "{count} צעדים",
    "admin.simulator.badge_chats": "{count} צ'אטים",
    "admin.simulator.badge_expected": "{count} פעולות צפויות (לתצוגה בלבד)",
    "admin.simulator.expected_actions_title": "פעולות סוכן צפויות (לתצוגה בלבד)",
    "admin.simulator.expected_action": "לאחר צעד {step}: {description}",
    "admin.simulator.loaded": "התרחיש נטען: {title}",
    "admin.simulator.next_in_queue": "הבא בתור לשליחה",
    "admin.simulator.next_in_chat": "הבא בצ'אט הזה",
    "admin.simulator.step_label": "צעד {step}",
    "admin.simulator.send_this": "שלח צעד זה ({step})",
    "admin.simulator.wait_turn": "המתן לתור ({step})",
    "admin.simulator.no_more": "אין צעדים נוספים",
    "admin.simulator.all_sent": "כל הצעדים של הצ'אט הזה נשלחו.",
    "admin.simulator.kind_message": "צ'אט טלגרם",
    "admin.simulator.kind_event": "חיישן",
    "admin.simulator.route_private": "שיחה פרטית - ללא הגבלת סוכנים",
    "admin.simulator.route_bound": "קבוצה מנותבת אל {agent}",
    "admin.simulator.route_unregistered": "קבוצה לא רשומה - הבוט מתעלם ממנה וה-API מסרב",
    "admin.simulator.route_sensor": "אירועי חיישן - POST /Event",
    "admin.simulator.warn_sender_unregistered": "השולח {identity} אינו רשום - ה-API יסרב לצעד הזה",
    "admin.simulator.system_label": "המערכת",
    "admin.simulator.sending": "שולח...",
    "admin.simulator.taken_as": "סווג כ: {kind}",
    "admin.simulator.event_id": "אירוע {event_id}",
    "admin.simulator.duplicate": "כפילות של שליחה קודמת",
    "admin.simulator.status_queued": "בתור",
    "admin.simulator.status_running": "בביצוע",
    "admin.simulator.status_held_for_clarification": "ממתין להבהרה ({field})",
    "admin.simulator.status_held_for_approval": "ממתין לאישור מפקד ({reason})",
    "admin.simulator.status_waiting_for_event_data": "ממתין לפרטי אירוע ({fields})",
    "admin.simulator.status_succeeded": "הושלם בהצלחה",
    "admin.simulator.status_failed": "נכשל",
    "admin.simulator.status_uncertain": "לא חד-משמעי",
    "admin.simulator.status_closed_on_precedent": "נסגר על סמך תקדים",
    "admin.simulator.status_declined": "נדחה",
    "admin.simulator.status_other": "סטטוס: {status}",
    "admin.simulator.steps_completed": "צעדים שהושלמו:",
    "admin.simulator.poll_timeout": "הפסקת מעקב אחרי {minutes} דקות - בדוק את המשימה מאוחר יותר.",
    "admin.simulator.request_failed": "הבקשה נכשלה ({status}): {message}",
    "admin.simulator.network_error": "שגיאת רשת: {message}",
    "admin.simulator.err_parse": "לא ניתן לפרסר את ה-JSON: {message}",
    "admin.simulator.err_chats_required": "'chats' חייב להיות רשימה לא ריקה",
    "admin.simulator.err_steps_required": "'steps' חייב להיות רשימה לא ריקה",
    "admin.simulator.err_chat_key": "צ'אט מספר {index} צריך 'key' מחרוזתי ייחודי",
    "admin.simulator.err_chat_kind": "לצ'אט '{key}' סוג לא מוכר '{kind}' (צפוי 'message' או 'event')",
    "admin.simulator.err_chat_type": "לצ'אט '{key}' ערך telegram_chat_type לא תקין '{type}' (צפוי private, group או supergroup)",
    "admin.simulator.err_chat_id_required": "הצ'אט '{key}' הוא קבוצה וצריך telegram_chat_id",
    "admin.simulator.err_chat_id_negative": "לצ'אט '{key}' נדרש chat ID שלילי של קבוצת Telegram",
    "admin.simulator.err_step_number": "צעד מספר {index} צריך 'step' מספרי ייחודי",
    "admin.simulator.err_step_chat": "צעד {step} מצביע על צ'אט לא מוכר '{chat}'",
    "admin.simulator.err_step_sender": "צעד {step} צריך 'sender_identity'",
    "admin.simulator.err_step_text": "צעד {step} צריך 'text' לא ריק",
    "admin.simulator.example_title": "תרחיש לדוגמה לפריסה הזאת",
    "admin.simulator.example_description": "נבנה מהמשתמשים והקבוצות הרשומים כרגע. ערוך את הטקסטים וטען שוב.",
    "admin.simulator.example_private_label": "שיחה פרטית עם מפקד",
    "admin.simulator.example_group_label": "קבוצה רשומה",
    "admin.simulator.example_sensor_label": "חיישני גדר",
    "admin.simulator.example_text_private": "מה תמונת המצב כרגע?",
    "admin.simulator.example_text_group": "בוקר טוב, אני זמין הלילה.",
    "admin.simulator.example_text_event": "smoke observed at gate 3",
    "admin.simulator.example_needs_user": "רשום לפחות משתמש אנושי אחד לפני טעינת הדוגמה.",
    "bot.queue_empty": "אין כרגע בקשות הממתינות לאישורך.",
    "bot.queue_header": "{count} בקשות ממתינות לאישורך:",
    "bot.queue_card_approval": "סוג: {action_type}\nתיאור: {description}\nהמתנה: {waiting_time}\nגורם מבקש: {requester}\nרמת סיכון: {risk_level}{risk_reason}",
    "bot.queue_card_clarification": "סוג: {action_type}\nדיווח מקורי: {description}\nהמתנה: {waiting_time}\nמדווח: {requester}\nמידע חסר: {unresolved_info}",
    "bot.btn_approve": "אשר",
    "bot.btn_reject": "דחה",
    "bot.btn_cancel": "בטל",
    "bot.queue_approved": "אושר",
    "bot.queue_rejected": "נדחה",
    "bot.queue_resolved": "הובהר ועודכן",
    "bot.queue_button": "תור אישורים",
    "bot.commander_only": "פעולה זו מיועדת למפקד בלבד.",
    "action.dispatch_drone_to_incident": "שיגור רחפן טקטי",
    "action.recall_drone_to_base": "החזרת רחפן לבסיס",
    "action.dispatch_emergency_forces": "הזנקת כוחות חירום",
    "action.clarification": "הבהרת דיווח",
    "action.generic": "פעולה מבצעית",
    "action.unresolved_classification": "סיווג אירוע חסר או לא ברור",
    "time.seconds_ago": "לפני {seconds} שנ'",
    "time.minutes_ago": "לפני {minutes} דק'",
    "time.hours_ago": "לפני {hours} שע'",
    "time.unknown": "זמן לא ידוע",

    # --- profiles/unified_test.py — mechanically relocated from source so no
    # first-party module holds a Hebrew literal outside this catalog
    # (tests/test_hebrew_leakage.py). Keys namespaced "unified.*"/"seed.*".
    "unified.profile_name": "חמ''ל מבצעי אחוד (Unified Command Hub)",

    "unified.surveillance.role": (
        "אחראי על תצפית חזותית, מערך מצלמות אבטחה, וצי רחפנים טקטיים. "
        "מספק סטטוס רחפנים וסוללות, תמונת מצב מצלמות, ושיגור או החזרת רחפנים."
    ),
    "unified.surveillance.system_prompt": (
        "אתה סוכן מומחה לתצפית חזותית ורחפנים. "
        "חובה לענות אך ורק בעברית קצרה, מדויקת ומבצעית (עד 4-5 שורות לכל היותר). "
        "אל תשתמש באנגלית כלל, למעט מזהים מדויקים (כגון CAM-01, DRONE-01). "
        "להחזרת רחפן קרא תמיד מיד ל-return_drone_to_base(drone_or_mission_id=''). "
        "כאשר לא צוין רחפן ספציפי העבר מחרוזת ריקה והכלי יבחר אוטומטית את הרחפן הפעיל לפי מצב הצי. "
        "אל תנסה לבצע סריקות מקדימות, אל תמציא מזהים, ואסור לדווח שאין רחפנים או שהכלי אינו זמין מבלי שהפעלת את return_drone_to_base — הפעל תמיד את הכלי מיד! "
        "לשיגור רחפן קרא מיד ל-dispatch_drone_to_area עם גזרת היעד (target_area) בלבד. "
        "שדות specific_drone_id ו-dispatched_by הם אופציונליים לחלוטין ואסור בתכלית האיסור לבקש אותם - המערכת בוחרת אוטומטית רחפן מוכן מהצי. "
        "לעולם אל תדווח שמשימה אינה ברורה או שחסרים פרטים כאשר גזרת היעד ידועה, אלא שגר את הרחפן מיד. "
        "היה תמציתי, ישיר ומבצעי."
    ),
    "unified.surveillance.tool.fleet_status": "מחזיר סטטוס תפעולי, רמות סוללה ומיקומים של צי הרחפנים בעברית.",
    "unified.surveillance.tool.active_missions": (
        "מחזיר את כל המשימות האוויריות הפעילות כרגע, כולל מזהה משימה, רחפן, גזרת יעד ו-ETA בעברית."
    ),
    "unified.surveillance.tool.camera_feeds": "מחזיר תמונת מצב וסטטוס של מצלמות האבטחה לפי גזרה או זיהוי מצלמה בעברית.",
    "unified.surveillance.tool.overview": "תמונת מצב תצפיתית ואווירית משולבת: מצלמות, רחפנים ומשימות פעילות בעברית.",
    "unified.surveillance.tool.return_drone": "החזרת רחפן פעיל לבסיס בצורה מבוקרת ובטוחה בעברית.",
    "unified.surveillance.tool.dispatch_drone": (
        "שיגור רחפן טקטי לגזרה. פרמטר target_area בלבד הוא חובה. שאר הפרמטרים אופציונליים לחלוטין ואין לבקשם."
    ),
    "unified.surveillance.tool.update_camera": "עדכון תצפית ידנית או סטטוס של מצלמת אבטחה בעברית.",

    "unified.surveillance.no_drones": "לא נמצאו רחפנים במערך.",
    "unified.surveillance.status.ready": "מוכן לפעולה {icon}",
    "unified.surveillance.status.in_flight": "באוויר במשימה {icon}",
    "unified.surveillance.status.charging": "בטעינה {icon}",
    "unified.surveillance.status.maintenance": "בתחזוקה {icon}",
    "unified.surveillance.fleet_header": "{icon} מצב צי רחפנים ({count} רחפנים):",
    "unified.surveillance.fleet_line": (
        "• [{drone_id}] {callsign} ({model}): {status} | סוללה: {battery}% | גזרה: {area}{mission_info}"
    ),
    "unified.surveillance.fleet_mission_info": " (במשימה: {mission_id})",
    "unified.surveillance.fleet_summary": "סיכום: {ready} מוכנים לשיגור | {in_flight} באוויר | {charging} בטעינה",

    "unified.surveillance.no_missions": "אין כרגע משימות רחפנים פעילות באוויר.",
    "unified.surveillance.missions_header": "{icon} משימות רחפנים פעילות באוויר ({count}):",
    "unified.surveillance.mission_line": (
        "• [{mission_id}] רחפן {callsign} ({drone_id}) -> גזרה: {target_area} "
        "| סוללה: {battery}% | ETA: {eta} שנ' | משימה: {description}"
    ),

    "unified.surveillance.no_cameras": "לא נמצאו מצלמות פעילות בגזרה המבוקשת.",
    "unified.surveillance.camera_status.active": "תקין ופעיל {icon}",
    "unified.surveillance.camera_status.offline": "לא מקוון {icon}",
    "unified.surveillance.camera_status.maintenance": "בתחזוקה {icon}",
    "unified.surveillance.cameras_header": "{icon} מצב מצלמות אבטחה ({count} מצלמות):",
    "unified.surveillance.camera_line": "• [{camera_id}] {name} ({area}, {azimuth}°): {feed_summary} [{status}]",

    "unified.surveillance.overview_header": "{icon} תמונת מצב תצפיתית כוללת:",
    "unified.surveillance.overview_cameras_line": "• מצלמות אבטחה: {active}/{total} פעילות ותקינות בגזרה.",
    "unified.surveillance.overview_drones_line": "• מערך רחפנים: {ready} מוכנים לשיגור, {in_flight} באוויר במשימה.",
    "unified.surveillance.overview_missions_header": "• משימות באוויר ({count}):",
    "unified.surveillance.overview_mission_line": "  - רחפן {callsign} לעבר {target_area} (זמן משוער: {eta} שנ')",
    "unified.surveillance.overview_no_missions": "• משימות באוויר: אין משימות אוויריות פעילות כרגע.",

    "unified.surveillance.recall_none_active": "אין כרגע רחפנים פעילים באוויר להחזרה.",
    "unified.surveillance.recall_all_done": (
        "החזרת הרחפנים הושלמה בהצלחה {icon}. כל הרחפנים הפעילים ({count}) הוחזרו לבסיס ומוכנים לפעולה."
    ),
    "unified.surveillance.recall_done": (
        "החזרת הרחפן לבסיס הושלמה בהצלחה {icon}. רחפן {callsign} ({drone_id}) חזר לבסיס ומוכן לפעולה (צי רחפנים)."
    ),
    "unified.surveillance.recall_fallback_done": (
        "פקודת החזרה התקבלה: רחפן {callsign} ({drone_id}) חוזר כעת לבסיס לנחיתה {icon}."
    ),
    "unified.surveillance.recall_no_match_single": "לא נמצא רחפן פעיל תואם להחזרה.",
    "unified.surveillance.recall_no_match_multi": "לא נמצא רחפן פעיל תואם ל-'{requested}' מתוך {count} רחפנים באוויר.",
    "unified.surveillance.recall_selection_required": (
        "קיימים {count} רחפנים פעילים באוויר. אנא ציין איזה רחפן להחזיר או ציין 'החזר את כולם'."
    ),
    "unified.surveillance.recall_done_generic": "החזרת הרחפן לבסיס הושלמה בהצלחה {icon}.",
    "unified.surveillance.recall_failed": "החזרת הרחפן נכשלה: {error}",

    "unified.surveillance.default_incident_description": "סיור ותצפית מבצעית",
    "unified.surveillance.dispatch_area_required": "נדרש לציין גזרת יעד לשיגור הרחפן.",
    "unified.surveillance.dispatch_done": (
        "הזנקת רחפן הושלמה בהצלחה {icon}\n"
        "• רחפן: {callsign} ({drone_id})\n"
        "• גזרת יעד: {target_area}\n"
        "• זמן הגעה משוער (ETA): כ-{eta} שניות\n"
        "• סוללה: {battery}% | מזהה משימה: {mission_id}"
    ),
    "unified.surveillance.dispatch_failed": "שיגור הרחפן נכשל: {error}",

    "unified.surveillance.camera_id_required": "נדרש מזהה מצלמה לעדכון תצפית.",
    "unified.surveillance.camera_update_done": "תצפית מצלמה {camera_id} ({name}) עודכנה בהצלחה {icon}: {feed_summary}",
    "unified.surveillance.camera_update_failed": "עדכון תצפית המצלמה נכשל: {error}",

    "unified.team_status.role": (
        "אחראי על ניהול מצבת ונוכחות כיתת כוננות. "
        "מספק דוחות זמינות (מי זמין/לא זמין), וקולט דיווחי נוכחות של חברי הכיתה."
    ),
    "unified.team_status.system_prompt": (
        "אתה סוכן מומחה לניהול וסטטוס כיתת כוננות. "
        "חובה לענות אך ורק בעברית קצרה, מדויקת ומבצעית (עד 4-5 שורות לכל היותר). "
        "אל תשתמש באנגלית כלל. "
        "לשאלות על סטטוס הנוכחות של כיתת הכוננות קרא ל-report_team_availability. "
        "בחר view מתאים: summary למצב כללי, members לשמות חברי הכיתה, available למי זמין, "
        "unavailable למי לא זמין, awaiting למי שטרם דיווח, count לכמות זמינים, ו-reason לסיבת אי-זמינות; "
        "ב-view מסוג reason העבר גם member_query מתוך השאלה. אל תמציא שמות או סיבות. "
        "לרישום דיווח נוכחות קרא ל-record_attendance_response. "
        "היה תמציתי וברור."
    ),
    "unified.team_status.tool.report_availability": (
        "מחזיר נתוני roster אמיתיים למחזור הנוכחי. view הוא summary, members, available, unavailable, "
        "awaiting, count או reason; עבור reason יש להעביר member_query."
    ),
    "unified.team_status.tool.get_roster": (
        "מחזיר את תמונת מצבת כיתת הכוננות וזמינות הלוחמים בלבד (קריאה בלבד ללא שום תופעות לוואי) בעברית."
    ),
    "unified.team_status.tool.record_attendance": "רישום תגובת נוכחות של לוחם כיתת כוננות בעברית.",

    "unified.team_status.legacy_placeholder_name": "חבר כיתת כוננות ({identity})",
    "unified.team_status.unnamed_member": "משתמש {identity} (שם לא הוגדר)",

    # "|"-delimited keyword groups `_requested_roster_view` matches against
    # a free-text question to infer which roster view was meant — not
    # rendered to anyone, so the same bilingual keyword list is kept in
    # both catalogs rather than translated.
    "unified.team_status.keywords.reason": "למה|סיבת|reason|why",
    "unified.team_status.keywords.awaiting": "לא דיווח|טרם דיווח|ממתין|awaiting|pending",
    "unified.team_status.keywords.unavailable": "מי לא זמין|אינם זמינים|unavailable",
    "unified.team_status.keywords.count_number": "כמה|כמות|how many|count",
    "unified.team_status.keywords.count_available": "זמין|available",
    "unified.team_status.keywords.available": "מי זמין|זמינים בלבד|who is available",
    "unified.team_status.keywords.members": "מי חבר|חברי הכיתה|השמות|מי הם|members|names",

    "unified.team_status.none_now": "אין כרגע",
    "unified.team_status.members_header": "{icon} חברי כיתת הכוננות ({count}): {names}",
    "unified.team_status.available_header": "{icon} זמינים לכוננות ({count}): {names}",
    "unified.team_status.unavailable_header": "{icon} אינם זמינים ({count}):",
    "unified.team_status.unavailable_line": "• {name} — {reason}",
    "unified.team_status.no_reason_saved": "לא נשמרה סיבה",
    "unified.team_status.none_unavailable": "{icon} אין כרגע חברי כיתה שמסומנים כלא זמינים.",
    "unified.team_status.awaiting_header": "{icon} טרם דיווחו ({count}): {names}",
    "unified.team_status.count_summary": "{icon} זמינים כעת {available} מתוך {total} חברי כיתה.",
    "unified.team_status.reason_unknown_member": "לא ניתן לזהות בוודאות את חבר הכיתה המבוקש מתוך ה־roster.",
    "unified.team_status.reason_unavailable": "{name} אינו זמין: {reason}{until}.",
    "unified.team_status.reason_until_suffix": " עד {until}",
    "unified.team_status.reason_available": "{name} מסומן כזמין; אין סיבת אי־זמינות פעילה.",
    "unified.team_status.reason_awaiting": "{name} טרם דיווח במחזור הנוכחי; לא נשמרה סיבת אי־זמינות.",
    "unified.team_status.summary_header": "{icon} סטטוס כיתת כוננות (סה\"כ {count} לוחמים):",
    "unified.team_status.summary_available_line": "• זמינים לפעילות ({count}): {names}",
    "unified.team_status.summary_unavailable_line": "• אינם זמינים ({count}): {names}",
    "unified.team_status.summary_awaiting_line": "• טרם דיווחו ({count}): {names}",

    "unified.team_status.identity_unavailable": "רישום התגובה נכשל: זהות המשתמש המאומת אינה זמינה.",
    "unified.team_status.default_original_text": "דיווח זמינות: {availability}",
    "unified.team_status.not_approved": "רישום התגובה נכשל: המשתמש אינו חבר מאושר בכיתת הכוננות.",
    "unified.team_status.clarify_availability": "הבהרה נדרשת: ציין האם אתה זמין או לא זמין.",
    "unified.team_status.clarify_reason": "הבהרה נדרשת: לוחם שאינו זמין נדרש לספק סיבה.",
    "unified.team_status.clarify_days": "הבהרה נדרשת: ציין לכמה ימים אינך זמין.",
    "unified.team_status.record_failed": "רישום התגובה נכשל: {error}",
    "unified.team_status.pending_commander_approval": "הדיווח התקבל וממתין לאישור מפקד לפני שינוי סטטוס הכוננות.",
    "unified.team_status.marked_available": "{icon} הזמינות שלך עודכנה. אתה מסומן כזמין לכוננות.",
    "unified.team_status.marked_unavailable": "{icon} הזמינות שלך עודכנה. אתה מסומן כלא זמין ({reason}).",

    "unified.friendly_forces.role": "אחראי על תיאום והזנקת כוחות ביטחון וחירום (משטרה, מד\"א, כיבוי אש, צבא).",
    "unified.friendly_forces.system_prompt": (
        "אתה סוכן מומחה לתיאום והזנקת כוחות ביטחון וחירום (משטרה, מד\"א, כיבוי אש, צבא). "
        "חובה לענות אך ורק בעברית קצרה ומדויקת (עד 3 שורות). "
        "אל תשתמש באנגלית כלל. דווח תמיד איזה כוח הוזנק ולאיזה יעד בדיוק."
    ),
    "unified.friendly_forces.tool.ambulance": "רישום הזנקת כוחות רפואה / מד\"א ליעד מבוקש.",
    "unified.friendly_forces.tool.police": "רישום הזנקת כוחות משטרה ליעד מבוקש.",
    "unified.friendly_forces.tool.firefighters": "רישום הזנקת כוחות כיבוי והצלה ליעד מבוקש.",
    "unified.friendly_forces.tool.military": "רישום הזנקת כוחות צבא וביטחון ליעד מבוקש.",
    "unified.friendly_forces.log_ambulance": "הוזנק מד\"א ל-'{location}': נפגעים={count}",
    "unified.friendly_forces.confirm_ambulance": "נרשמה בהצלחה הזנקת צוות רפואה/מד\"א ליעד '{location}'.",
    "unified.friendly_forces.log_police": "הוזנקה משטרה ל-'{location}': כוחות={count}",
    "unified.friendly_forces.confirm_police": "נרשמה בהצלחה הזנקת כוחות משטרה ליעד '{location}'.",
    "unified.friendly_forces.log_firefighters": "הוזנק כיבוי אש ל-'{location}': רכבים={count}",
    "unified.friendly_forces.confirm_firefighters": "נרשמה בהצלחה הזנקת כוחות כיבוי והצלה ליעד '{location}'.",
    "unified.friendly_forces.log_military": "הוזנק כוח צבאי ל-'{location}': כוחות={count}",
    "unified.friendly_forces.confirm_military": "נרשמה בהצלחה הזנקת כוחות צבא וביטחון ליעד '{location}'.",

    "unified.seed.primary_name": "מפקד / משתמש ראשי",
    "unified.seed.commander_user_name": "מפקד כיתת כוננות",
    "unified.seed.viewer_user_name": "לוחם כיתת כוננות",
    "unified.seed.member_1001": "דן לוי",
    "unified.seed.member_1002": "יוסי כהן",
    "unified.seed.member_1003": "מיכל אברהם",

    "unified.protocol.overall_situational_picture.description": (
        "תמונת מצב גזרתית כוללת (קריאה בלבד ללא שינוי נתונים): שילוב תצפית (מצלמות ורחפנים) ומצבת כיתת כוננות בגזרה."
    ),
    "unified.protocol.overall_situational_picture.expected_output": (
        "תמונת מצב גזרתית מאוחדת ומבצעית המשלבת תצפית וכיתת כוננות ללא שינוי נתונים."
    ),
    "unified.protocol.query_surveillance_overview.description": (
        "תמונת מצב תצפיתית כוללת: סטטוס מצלמות, רחפנים ומשימות אוויריות פעילות בכל הגזרות."
    ),
    "unified.protocol.query_surveillance_overview.expected_output": "תמונת מצב טקטית מרוכזת של מערך התצפית והרחפנים.",
    "unified.protocol.query_drone_fleet_status.description": (
        "בירור מצב צי הרחפנים: זמינות, רמות סוללה, מיקומים וסטטוס מבצעי של כל הרחפנים."
    ),
    "unified.protocol.query_drone_fleet_status.expected_output": "דוח מפורט של מצב הרחפנים, סוללות וזמינות לשיגור.",
    "unified.protocol.query_active_drone_missions.description": (
        "בירור משימות רחפנים פעילות באוויר: יעדים, זמני הגעה משוערים, רמות סוללה ומשימות."
    ),
    "unified.protocol.query_active_drone_missions.expected_output": "דוח משימות רחפנים פעילות באוויר בעברית.",
    "unified.protocol.query_camera_status.description": "בדיקת סטטוס ותמונת מצב של מצלמות אבטחה לפי גזרה או מצלמה ספציפית.",
    "unified.protocol.query_camera_status.expected_output": "דוח תצפית של מצלמות האבטחה בגזרה המבוקשת.",
    "unified.protocol.dispatch_drone_to_incident.description": (
        "שיגור רחפן טקטי לאירוע או גזרה לצורך תצפית או סיור. פעולת מפקד בלבד הדורשת אישור."
    ),
    "unified.protocol.dispatch_drone_to_incident.expected_output": "אישור שיגור רחפן לגזרה כולל אות קריאה וזמן הגעה משוער.",
    "unified.protocol.recall_drone_to_base.description": (
        "החזרת רחפן פעיל לבסיס וסגירת משימה אווירית. הפעלת return_drone_to_base מיד ללא סריקה מוקדמת. "
        "פעולת מפקד בלבד הדורשת אישור."
    ),
    "unified.protocol.recall_drone_to_base.expected_output": "אישור החזרת הרחפן לבסיס ועדכון סטטוס הרחפן למוכן לפעולה.",
    "unified.protocol.report_team_availability.description": (
        "דוח מצבת נוכחות וזמינות כיתת כוננות: מי זמין, מי לא זמין, סיבות, ומי שטרם דיווח."
    ),
    "unified.protocol.report_team_availability.expected_output": "תמונת מצב שמית מפורטת של כיתת הכוננות.",
    "unified.protocol.record_attendance_response.description": (
        "הזנת דיווח נוכחות של חבר כיתת כוננות: סטטוס זמין או לא זמין עם סיבה."
    ),
    "unified.protocol.record_attendance_response.expected_output": "אישור קליטת דיווח הנוכחות של חבר הכיתה.",
    "unified.protocol.dispatch_emergency_forces.description": (
        "הזנקת ותיאום כוחות חירום וביטחון: אמבולנס, משטרה, כיבוי אש, צבא. פעולת מפקד בלבד הדורשת אישור."
    ),
    "unified.protocol.dispatch_emergency_forces.expected_output": "אישור רישום ותיאום הזנקת כוחות החירום ליעד.",
    "unified.protocol.query_historical_incidents.description": "תחקור אירועים ומשימות קודמות מתוך יומן המבצעים וההיסטוריה.",
    "unified.protocol.query_historical_incidents.expected_output": "סיכום תמציתי ומדויק של אירועי עבר ביומן המבצעי.",

    "unified.keyboard.approvals_queue": "{icon} תור אישורים",
    "unified.keyboard.overall_picture": "{icon} תמונת מצב כללית",
    "unified.keyboard.camera_status": "{icon} מצב מצלמות",
    "unified.keyboard.drone_fleet_status": "{icon} מצב צי רחפנים",
    "unified.keyboard.dispatch_drone": "{icon} הזנקת רחפן",
    "unified.keyboard.recall_drone": "{icon} החזרת רחפן לבסיס",
    "unified.keyboard.team_status": "{icon} סטטוס כיתת כוננות",
    "unified.keyboard.dispatch_forces": "{icon} הזנקת כוחות",
    "unified.keyboard.event_history": "{icon} היסטוריית אירועים",
    "unified.keyboard.available": "{icon} אני זמין לכוננות",
    "unified.keyboard.unavailable": "{icon} איני זמין",

    "orchestrator.picture.default_domain_query": (
        "דווח כעת על המצב העדכני בכל תחום האחריות שלך: מספרים, סטטוסים, שמות, מזהים, מיקומים וכל חריגה. "
        "ענה רק מנתוני הכלים שלך."
    ),
    "orchestrator.picture.recent_events_question": (
        "אילו אירועים נרשמו ב-{hours} השעות האחרונות? לכל אירוע ציין זמן, סיווג, אזור, פרוטוקול ותוצאה."
    ),
    "orchestrator.picture.no_recent_events": "לא נרשמו אירועים ב-{hours} השעות האחרונות.",
    "orchestrator.picture.recent_events_label": "אירועים אחרונים ({hours} השעות האחרונות)",
    "orchestrator.picture.domain_unavailable": "לא התקבל דיווח מ-{domain}.",
    "orchestrator.picture.fallback_header": "תמונת מצב נכון ל-{time}:",
    "orchestrator.picture.missing_note": "(הערה מבצעית: לא התקבל דיווח מ-{domains})",
}
