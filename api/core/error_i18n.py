"""Arabic for the API's own messages (errors and short confirmations).

Requests from the Arabic site carry Accept-Language: ar (the UI sets it
from the page language). ArabicApiMessagesMiddleware then swaps the
API's English messages - this codebase's own and the common Django/DRF
built-ins - for the Arabic below. Django's LocaleMiddleware is not used:
it would also localize number/date formatting in PDFs rendered during
the request. A message
with variable parts is listed with {placeholders}; a long message split
over several source lines is matched by its start ("prefix" entries).
Anything not listed falls back to a general Arabic message for its
status code, so an Arabic user never sees an English error.
"""
import json
import re


EXACT = {
    "A Free Trial must be priced at 0.": "يجب أن يكون سعر التجربة المجانية صفرًا.",
    "A Free Trial must grant exactly 2 Assessment Slots.": "يجب أن تمنح التجربة المجانية خانتي تقييم بالضبط.",
    "A candidate with this email already exists in your company.": "يوجد مرشح بهذا البريد الإلكتروني في شركتك بالفعل.",
    "A candidate with this email already exists in your scope.": "يوجد مرشح بهذا البريد الإلكتروني لديك بالفعل.",
    "A candidate with this passport ID already exists in your scope.": "يوجد مرشح برقم جواز السفر هذا لديك بالفعل.",
    "A company can have at most 20 addresses.": "يمكن للشركة إضافة 20 عنوانًا كحد أقصى.",
    "A company can have at most 8 roles.": "يمكن للشركة إضافة 8 مجالات عمل كحد أقصى.",
    "A company has one Main Office. Change the current one first.": "للشركة مكتب رئيسي واحد فقط. غيّر المكتب الحالي أولًا.",
    "A document file is required.": "ملف المستند مطلوب.",
    "A payment method is required for this plan.": "طريقة الدفع مطلوبة لهذه الباقة.",
    "A trade license must be uploaded before the company can be approved.": "يجب رفع الرخصة التجارية قبل الموافقة على الشركة.",
    "A user with this email already exists. Admin accounts cannot be created over an existing account.":
        "يوجد مستخدم بهذا البريد الإلكتروني بالفعل، ولا يمكن إنشاء حساب مسؤول فوق حساب قائم.",
    "Account deleted": "تم حذف الحساب",
    "Account not found": "الحساب غير موجود",
    "Address not found": "العنوان غير موجود",
    "Admin user created successfully": "تم إنشاء حساب المسؤول بنجاح",
    "Admin user deleted successfully": "تم حذف حساب المسؤول بنجاح",
    "Admin user not found": "حساب المسؤول غير موجود",
    "Agreement not found": "الاتفاقية غير موجودة",
    "An audio recording is required.": "التسجيل الصوتي مطلوب.",
    "An unexpected error occurred": "حدث خطأ غير متوقع",
    "At least 2 comparable candidates are required.": "يلزم وجود مرشحَين قابلين للمقارنة على الأقل.",
    "At least one skill is required.": "يجب إضافة مهارة واحدة على الأقل.",
    "Authentication required.": "يلزم تسجيل الدخول.",
    "Average recalculated successfully": "تمت إعادة احتساب المتوسط بنجاح",
    "Both agreements must be accepted to continue.": "يجب الموافقة على الاتفاقيتين للمتابعة.",
    "Candidate not found": "المرشح غير موجود",
    "Cannot delete the last Super Admin": "لا يمكن حذف آخر مشرف عام",
    "Cannot delete your own account": "لا يمكنك حذف حسابك الخاص",
    "Cannot disable the last active Super Admin": "لا يمكن تعطيل آخر مشرف عام نشط",
    "Cannot disable your own account": "لا يمكنك تعطيل حسابك الخاص",
    "Certificate issuance is disabled for this evaluation tier": "إصدار الشهادات غير متاح لمستوى التقييم هذا",
    "Company not found": "الشركة غير موجودة",
    "Company owner not found": "مالك الشركة غير موجود",
    "Company registration number already exists.": "رقم تسجيل الشركة مستخدم بالفعل.",
    "Could not attach payment method": "تعذّرت إضافة طريقة الدفع",
    "Could not create customer": "تعذّر إنشاء حساب العميل",
    "Could not create payment intent": "تعذّر بدء عملية الدفع",
    "Could not create setup intent": "تعذّر إعداد طريقة الدفع",
    "Could not detach payment method": "تعذّرت إزالة طريقة الدفع",
    "Could not verify Google sign-in. Please try again.": "تعذّر التحقق من تسجيل الدخول عبر Google. حاول مرة أخرى.",
    "Could not verify this payment with Stripe. Please retry later.": "تعذّر التحقق من هذه الدفعة. حاول مرة أخرى لاحقًا.",
    "Country is required.": "الدولة مطلوبة.",
    "Creation failed because one of the submitted unique values already exists.": "تعذّر الإنشاء لأن إحدى القيم المدخلة مستخدمة بالفعل.",
    "Current password is incorrect.": "كلمة المرور الحالية غير صحيحة.",
    "Default payment method updated": "تم تحديث طريقة الدفع الافتراضية",
    "Document request not found": "طلب المستند غير موجود",
    "Documents rejected successfully": "تم رفض المستندات",
    "Duplicate agreement types in request.": "أنواع الاتفاقيات مكررة في الطلب.",
    "Each role must be 30 characters or fewer.": "يجب ألا يتجاوز كل مجال عمل 30 حرفًا.",
    "Each role must be a non-empty string.": "يجب ألا يكون مجال العمل فارغًا.",
    "Either price_id or amount must be provided": "يجب تحديد الباقة أو المبلغ",
    "Email verification is required before login.": "يجب تأكيد البريد الإلكتروني قبل تسجيل الدخول.",
    "Employer account created successfully.": "تم إنشاء حساب صاحب العمل بنجاح.",
    "Employer not found": "صاحب العمل غير موجود",
    "Evaluation cancelled successfully": "تم إلغاء التقييم بنجاح",
    "Evaluation does not belong to this candidate": "هذا التقييم لا يخص هذا المرشح",
    "Evaluation not found": "التقييم غير موجود",
    "Evaluation readiness corrections cannot be deleted.": "لا يمكن حذف تصحيحات الجاهزية.",
    "Evaluation readiness decision records are immutable once generated.": "لا يمكن تعديل سجلات قرار الجاهزية بعد إنشائها.",
    "Evaluation readiness decision records cannot be deleted.": "لا يمكن حذف سجلات قرار الجاهزية.",
    "Evaluation rescheduled successfully": "تم تغيير موعد التقييم بنجاح",
    "Expected steps must be a list": "يجب أن تكون الخطوات المتوقعة قائمة",
    "Failed to cancel subscription": "تعذّر إلغاء الاشتراك",
    "Failed to create subscription. Please check logs.": "تعذّر إنشاء الاشتراك.",
    "Failed to generate the signed document. Please try again.": "تعذّر إنشاء المستند الموقّع. حاول مرة أخرى.",
    "Generated evaluation reports are immutable. Regeneration must create a new report version.":
        "لا يمكن تعديل التقارير بعد إنشائها، وتؤدي إعادة الإنشاء إلى إصدار جديد من التقرير.",
    "Generated evaluation reports cannot be deleted.": "لا يمكن حذف التقارير بعد إنشائها.",
    "Give an email, user_id or ip.": "أدخل بريدًا إلكترونيًا أو معرّف مستخدم أو عنوان IP.",
    "Give candidate_id, passport_id, session_id or evaluation_id.": "أدخل معرّف المرشح أو رقم الجواز أو معرّف الجلسة أو التقييم.",
    "Google sign-in is not configured.": "تسجيل الدخول عبر Google غير مُعدّ.",
    "If an account exists with this email, you will receive a password reset link.":
        "إذا كان هناك حساب بهذا البريد الإلكتروني، فسيصلك رابط لإعادة تعيين كلمة المرور.",
    "If the email exists and is unverified, a verification code will be sent.":
        "إذا كان البريد الإلكتروني مسجلًا وغير مؤكد، فسيُرسل إليه رمز تحقق.",
    "Image must be smaller than 5MB": "يجب أن يكون حجم الصورة أقل من 5 ميجابايت",
    "Incorrect password": "كلمة المرور غير صحيحة",
    "Interview configuration not found": "إعداد المقابلة غير موجود",
    "Invalid document type": "نوع المستند غير صالح",
    "Invalid email or verification code.": "البريد الإلكتروني أو رمز التحقق غير صحيح.",
    "Invalid invitation token": "رابط الدعوة غير صالح",
    "Invalid or expired password reset token.": "رابط إعادة تعيين كلمة المرور غير صالح أو منتهي الصلاحية.",
    "Invalid user role": "دور المستخدم غير صالح",
    "Invitation accepted successfully. You can now log in.": "تم قبول الدعوة بنجاح، ويمكنك الآن تسجيل الدخول.",
    "Invitation cancelled successfully": "تم إلغاء الدعوة بنجاح",
    "Invitation has expired": "انتهت صلاحية الدعوة",
    "Invitation not found or cannot be cancelled": "الدعوة غير موجودة أو لا يمكن إلغاؤها",
    "Invitation sent successfully": "تم إرسال الدعوة بنجاح",
    "Keywords must be a list": "يجب أن تكون الكلمات المفتاحية قائمة",
    "Max retries must be at least 1 when retries are enabled": "يجب ألا يقل الحد الأقصى للمحاولات عن 1 عند تفعيلها",
    "Maximum 20 skills allowed.": "الحد الأقصى 20 مهارة.",
    "Maximum resend attempts reached. Please start over.": "تم بلوغ الحد الأقصى لإعادة الإرسال. يرجى البدء من جديد.",
    "Message sent to applicant": "تم إرسال الرسالة إلى مقدم الطلب",
    "New date must be in the future": "يجب أن يكون الموعد الجديد في المستقبل",
    "New password must be different from current password.": "يجب أن تختلف كلمة المرور الجديدة عن الحالية.",
    "New passwords do not match.": "كلمتا المرور الجديدتان غير متطابقتين.",
    "New price not found": "الباقة الجديدة غير موجودة",
    "No MeritLense account found for this email. Please sign up first.": "لا يوجد حساب في MeritLense بهذا البريد الإلكتروني. يرجى التسجيل أولًا.",
    "No account found.": "لم يتم العثور على حساب.",
    "No candidate found.": "لم يتم العثور على مرشح.",
    "No certificate found for this ID.": "لا توجد شهادة بهذا المعرّف.",
    "No certificate has been generated yet.": "لم تصدر أي شهادة بعد.",
    "No company found for this account.": "لا توجد شركة مرتبطة بهذا الحساب.",
    "No company found for this user": "لا توجد شركة مرتبطة بهذا المستخدم",
    "No company profile found": "ملف الشركة غير موجود",
    "No document provided": "لم يتم إرفاق مستند",
    "No employer PDF has been generated yet.": "لم يصدر تقرير صاحب العمل بعد.",
    "No evaluation report has been generated yet.": "لم يصدر تقرير التقييم بعد.",
    "No evaluator rating has been submitted yet.": "لم يُرسَل تقييم المقيّم بعد.",
    "No image provided": "لم يتم إرفاق صورة",
    "No pending signing request found for this reference.": "لا يوجد طلب توقيع معلّق بهذا المرجع.",
    "No preview available for this agreement type.": "لا تتوفر معاينة لهذا النوع من الاتفاقيات.",
    "No public preview available for this agreement type.": "لا تتوفر معاينة عامة لهذا النوع من الاتفاقيات.",
    "No readiness legal record has been generated yet.": "لم يصدر السجل القانوني للجاهزية بعد.",
    "No reusable certificate was found for this passport ID.": "لا توجد شهادة قابلة لإعادة الاستخدام لرقم الجواز هذا.",
    "No scoring summary has been generated yet.": "لم يصدر ملخص الدرجات بعد.",
    "No signed agreement found for this ID.": "لا توجد اتفاقية موقّعة بهذا المعرّف.",
    "No speech was detected in the recording.": "لم يُرصد أي كلام في التسجيل.",
    "Not a valid IP address.": "عنوان IP غير صالح.",
    "Not authorized to view this audit trail.": "غير مصرح لك بعرض سجل التدقيق هذا.",
    "Not authorized.": "غير مصرح لك.",
    "Only PDF, JPG, JPEG, or PNG files are accepted.": "يُقبل فقط ملفات PDF أو JPG أو JPEG أو PNG.",
    "Only admins can delete scoring rule sets": "يمكن للمسؤولين فقط حذف قواعد الاحتساب",
    "Only an admin reviewer can view the review queue.": "يمكن للمراجع المسؤول فقط عرض قائمة المراجعة.",
    "Only cancelled subscriptions can be reactivated": "يمكن إعادة تفعيل الاشتراكات الملغاة فقط",
    "Only the company administrator can customize the dashboard.": "يمكن لمسؤول الشركة فقط تخصيص لوحة التحكم.",
    "Only the trade license can be uploaded while company access is restricted.": "يمكن رفع الرخصة التجارية فقط أثناء تقييد الوصول إلى الشركة.",
    "Pass threshold cannot exceed max score": "لا يمكن أن يتجاوز حد النجاح الدرجة القصوى",
    "Passport ID already exists.": "رقم جواز السفر مستخدم بالفعل.",
    "Password changed successfully. Please log in again with your new password.": "تم تغيير كلمة المرور بنجاح. يرجى تسجيل الدخول مجددًا بكلمة المرور الجديدة.",
    "Password has been reset successfully. You can now log in with your new password.": "تمت إعادة تعيين كلمة المرور بنجاح، ويمكنك الآن تسجيل الدخول بكلمة المرور الجديدة.",
    "Password is required for new users.": "كلمة المرور مطلوبة للمستخدمين الجدد.",
    "Password must contain at least one number.": "يجب أن تحتوي كلمة المرور على رقم واحد على الأقل.",
    "Password must contain at least one symbol.": "يجب أن تحتوي كلمة المرور على رمز واحد على الأقل.",
    "Password reset link has expired. Please request a new one.": "انتهت صلاحية رابط إعادة التعيين. يرجى طلب رابط جديد.",
    "This verification code has expired. Please request a new one.": "انتهت صلاحية رمز التحقق. يرجى طلب رمز جديد.",
    "Passwords do not match.": "كلمتا المرور غير متطابقتين.",
    "Please upload your company stamp in Company Profile before signing this agreement.": "يرجى رفع ختم الشركة في ملف الشركة قبل توقيع هذه الاتفاقية.",
    "Price not found or inactive": "الباقة غير موجودة أو غير مفعّلة",
    "Profile not found": "الملف الشخصي غير موجود",
    "Profile picture removed": "تمت إزالة الصورة الشخصية",
    "Provide either recording_file or recording_path.": "أرفق التسجيل.",
    "Provide event_type or enable automatic integrity analysis input.": "حدد نوع الحدث أو فعّل التحليل التلقائي للنزاهة.",
    "Quantity updated successfully": "تم تحديث الكمية بنجاح",
    "Question not found": "السؤال غير موجود",
    "Question not found in this session": "السؤال غير موجود في هذه الجلسة",
    "Registration failed because one of the submitted unique values already exists.": "تعذّر التسجيل لأن إحدى القيم المدخلة مستخدمة بالفعل.",
    "Registration successful. Please check your email for the 5-digit verification code.": "تم التسجيل بنجاح. يرجى مراجعة بريدك الإلكتروني للحصول على رمز التحقق المكوّن من 5 أرقام.",
    "Rejection reason is required when rejecting documents.": "سبب الرفض مطلوب عند رفض المستندات.",
    "Rejection reason is required when rejecting verification.": "سبب الرفض مطلوب عند رفض التحقق.",
    "Response not found": "الإجابة غير موجودة",
    "Scheduled date must be in the future": "يجب أن يكون الموعد في المستقبل",
    "Scheduled start time must be in the future": "يجب أن يكون وقت البدء في المستقبل",
    "Self-service account deletion is only available for individual accounts": "حذف الحساب ذاتيًا متاح لحسابات الأفراد فقط",
    "Stripe confirmed payment, but MeritLense could not finish reconciliation. Please contact support.":
        "تم تأكيد الدفع، لكن تعذّر على MeritLense إكمال المطابقة. يرجى التواصل مع الدعم.",
    "Subscription cancelled successfully": "تم إلغاء الاشتراك بنجاح",
    "Subscription not found": "الاشتراك غير موجود",
    "Subscription reactivated successfully": "تمت إعادة تفعيل الاشتراك بنجاح",
    "Subscription synced successfully": "تمت مزامنة الاشتراك بنجاح",
    "Team member not found": "عضو الفريق غير موجود",
    "Team member profile not found": "ملف عضو الفريق غير موجود",
    "Team member removed successfully": "تمت إزالة عضو الفريق بنجاح",
    "The Main Office cannot be removed. Edit it, or make another address the Main Office first.":
        "لا يمكن حذف المكتب الرئيسي. عدّله، أو اجعل عنوانًا آخر هو المكتب الرئيسي أولًا.",
    "The Stripe customer does not match the recorded payment; no changes were made.": "بيانات العميل لا تطابق الدفعة المسجلة، ولم يتم إجراء أي تغيير.",
    "The agreement terms have changed. Please review and start signing again.": "تغيّرت شروط الاتفاقية. يرجى مراجعتها والبدء بالتوقيع من جديد.",
    "The amount confirmed by Stripe does not match the recorded payment; no changes were made.": "المبلغ المؤكد لا يطابق الدفعة المسجلة، ولم يتم إجراء أي تغيير.",
    "The currency confirmed by Stripe does not match the recorded payment; no changes were made.": "العملة المؤكدة لا تطابق الدفعة المسجلة، ولم يتم إجراء أي تغيير.",
    "The email could not be sent. Copy the link and send it another way.": "تعذّر إرسال البريد الإلكتروني. انسخ الرابط وأرسله بطريقة أخرى.",
    "The evaluation must belong to the same interview session": "يجب أن ينتمي التقييم إلى جلسة المقابلة نفسها",
    "The other participant is not available yet.": "الطرف الآخر غير متاح بعد.",
    "This account has been disabled.": "تم تعطيل هذا الحساب.",
    "This agreement has not been signed yet.": "لم يتم توقيع هذه الاتفاقية بعد.",
    "This agreement type has no downloadable document.": "لا يتوفر مستند قابل للتنزيل لهذا النوع من الاتفاقيات.",
    "This assessment has already ended; its link can no longer be used.": "انتهى هذا التقييم ولم يعد بالإمكان استخدام رابطه.",
    "This candidate has no email address.": "لا يوجد بريد إلكتروني لهذا المرشح.",
    "This company (or its admin email) has already used a Free Trial.": "استخدمت هذه الشركة (أو بريد مسؤولها) التجربة المجانية من قبل.",
    "This document request is no longer pending.": "لم يعد طلب المستند هذا معلّقًا.",
    "This interview session is closed": "جلسة المقابلة هذه مغلقة",
    "This invoice has no PDF or hosted link yet - it may still be pending from Stripe.": "لا يتوفر ملف PDF أو رابط لهذه الفاتورة بعد، فقد تكون قيد المعالجة.",
    "This live call has ended": "انتهت هذه المكالمة",
    "This package has subscription history and cannot be permanently deleted - deactivate it instead.": "لهذه الباقة سجل اشتراكات ولا يمكن حذفها نهائيًا، يمكنك إيقافها بدلًا من ذلك.",
    "This plan is not available for your account type": "هذه الباقة غير متاحة لنوع حسابك",
    "This result is awaiting human review and will be released after approval.": "هذه النتيجة بانتظار المراجعة البشرية وستُعرض بعد الموافقة عليها.",
    "This rule set has already been used and can no longer be modified": "استُخدمت قواعد الاحتساب هذه بالفعل ولم يعد بالإمكان تعديلها",
    "This session is no longer valid because the password was changed. Please log in again.": "لم تعد هذه الجلسة صالحة لأن كلمة المرور تغيّرت. يرجى تسجيل الدخول مجددًا.",
    "Total questions must be at least 1": "يجب ألا يقل عدد الأسئلة عن 1",
    "Transcription failed. Please try again.": "تعذّر تحويل الصوت إلى نص. حاول مرة أخرى.",
    "Unknown agreement type.": "نوع اتفاقية غير معروف.",
    "Unknown choice.": "خيار غير معروف.",
    "Upload the trade license through the document upload endpoint.": "ارفع الرخصة التجارية من قسم رفع المستندات.",
    "User not found": "المستخدم غير موجود",
    "User profile not found": "ملف المستخدم غير موجود",
    "User status updated successfully": "تم تحديث حالة المستخدم بنجاح",
    "User with this email already exists.": "يوجد مستخدم بهذا البريد الإلكتروني بالفعل.",
    "Verification code sent successfully.": "تم إرسال رمز التحقق بنجاح.",
    "Weighted indicators must add up to more than zero": "يجب أن يكون مجموع أوزان المؤشرات أكبر من صفر",
    "You already have a candidate with this email.": "لديك مرشح بهذا البريد الإلكتروني بالفعل.",
    "You already have an active subscription. Please cancel it first.": "لديك اشتراك نشط بالفعل. يرجى إلغاؤه أولًا.",
    "You do not have access to cancel this interview session": "لا تملك صلاحية إلغاء جلسة المقابلة هذه",
    "You do not have access to reschedule this interview session": "لا تملك صلاحية تغيير موعد جلسة المقابلة هذه",
    "You do not have access to these task observation results": "لا تملك صلاحية الوصول إلى نتائج المهام هذه",
    "You do not have access to this candidate": "لا تملك صلاحية الوصول إلى هذا المرشح",
    "You do not have access to this interview report": "لا تملك صلاحية الوصول إلى تقرير المقابلة هذا",
    "You do not have access to this interview response": "لا تملك صلاحية الوصول إلى هذه الإجابة",
    "You do not have access to this interview session": "لا تملك صلاحية الوصول إلى جلسة المقابلة هذه",
    "You do not have access to this live call": "لا تملك صلاحية الوصول إلى هذه المكالمة",
    "You do not have access to this report": "لا تملك صلاحية الوصول إلى هذا التقرير",
    "You do not have permission to create scoring rule sets": "لا تملك صلاحية إنشاء قواعد الاحتساب",
    "You do not have permission to manage interview setup": "لا تملك صلاحية إدارة إعداد المقابلات",
    "You do not have permission to update scoring rule sets": "لا تملك صلاحية تعديل قواعد الاحتساب",
    "You do not have permission to view the question bank": "لا تملك صلاحية عرض بنك الأسئلة",
    "You must confirm you are authorized to legally bind this organization.": "يجب أن تؤكد أنك مخوّل قانونيًا بإلزام هذه الجهة.",
    "Your Google account email is not verified.": "البريد الإلكتروني لحسابك في Google غير مؤكد.",
    "Your company account is restricted until its trade license is approved.": "حساب شركتك مقيّد حتى تتم الموافقة على الرخصة التجارية.",
    "Your team permissions do not allow evaluation access": "لا تسمح صلاحياتك في الفريق بالوصول إلى التقييمات",
    # Django / DRF built-ins
    "This field is required.": "هذا الحقل مطلوب.",
    "This field may not be blank.": "لا يمكن ترك هذا الحقل فارغًا.",
    "This field may not be null.": "هذا الحقل مطلوب.",
    "Enter a valid email address.": "أدخل بريدًا إلكترونيًا صالحًا.",
    "Enter a valid URL.": "أدخل رابطًا صالحًا.",
    "Enter a valid date.": "أدخل تاريخًا صالحًا.",
    "A valid integer is required.": "أدخل رقمًا صحيحًا.",
    "A valid number is required.": "أدخل رقمًا صالحًا.",
    "Must be a valid boolean.": "قيمة غير صالحة.",
    "Not a valid string.": "نص غير صالح.",
    "Not found.": "العنصر غير موجود.",
    "No Candidate matches the given query.": "المرشح غير موجود.",
    "Authentication credentials were not provided.": "يرجى تسجيل الدخول للمتابعة.",
    "Given token not valid for any token type": "انتهت جلستك. يرجى تسجيل الدخول مجددًا.",
    "Token is invalid or expired": "انتهت جلستك. يرجى تسجيل الدخول مجددًا.",
    "Token is invalid": "انتهت جلستك. يرجى تسجيل الدخول مجددًا.",
    "Token is expired": "انتهت جلستك. يرجى تسجيل الدخول مجددًا.",
    "User not found": "المستخدم غير موجود",
    "No active account found with the given credentials": "البريد الإلكتروني أو كلمة المرور غير صحيحة.",
    "Incorrect authentication credentials.": "بيانات تسجيل الدخول غير صحيحة.",
    "You do not have permission to perform this action.": "لا تملك صلاحية تنفيذ هذا الإجراء.",
    "Invalid page.": "صفحة غير صالحة.",
    "The submitted data was not a file. Check the encoding type on the form.": "البيانات المرسلة ليست ملفًا.",
    "No file was submitted.": "لم يتم إرسال أي ملف.",
    "The submitted file is empty.": "الملف المرسل فارغ.",
    "Upload a valid image. The file you uploaded was either not an image or a corrupted image.": "ارفع صورة صالحة؛ الملف المرفوع ليس صورة أو أنه تالف.",
    "Request was throttled.": "طلبات كثيرة جدًا. يرجى الانتظار قليلًا.",
    "Unsupported media type in request.": "نوع الملف غير مدعوم.",
    "Malformed request.": "الطلب غير صالح.",
    "A server error occurred.": "حدث خطأ في الخادم. يرجى المحاولة مرة أخرى لاحقًا.",
    # messages added since the list above
    "Only the assessment's owner can send its link.": "يمكن لمالك التقييم فقط إرسال رابطه.",
    "This password reset link is invalid or has expired.": "رابط إعادة تعيين كلمة المرور غير صالح أو منتهي الصلاحية.",
}

# Messages with variable parts: {name} matches any text, reused in Arabic.
TEMPLATES = {
    "Cannot cancel evaluation with status {status}": "لا يمكن إلغاء تقييم حالته {status}",
    "Cannot complete evaluation with status {status}": "لا يمكن إكمال تقييم حالته {status}",
    "Cannot reschedule evaluation with status {status}": "لا يمكن تغيير موعد تقييم حالته {status}",
    "Cannot reconcile a payment with status {status}.": "لا يمكن مطابقة دفعة حالتها {status}.",
    "Invalid job role code: {code}": "رمز الدور الوظيفي غير صالح: {code}",
    "Invalid permission: {perm}": "صلاحية غير صالحة: {perm}",
    "Invoice sent to {email}.": "تم إرسال الفاتورة إلى {email}.",
    "Please wait {seconds}s before requesting a new code.": "يرجى الانتظار {seconds} ثانية قبل طلب رمز جديد.",
    "Score for {area} must be between 0 and 100": "يجب أن تكون درجة {area} بين 0 و100",
    "Synced {created} new prices, updated {updated} existing prices": "تمت مزامنة {created} باقة جديدة وتحديث {updated} باقة",
    "Synced {count} subscriptions": "تمت مزامنة {count} اشتراك",
    "Ensure this field has no more than {n} characters.": "يجب ألا يتجاوز هذا الحقل {n} حرفًا.",
    "Ensure this field has at least {n} characters.": "يجب ألا يقل هذا الحقل عن {n} أحرف.",
    "Ensure this value is less than or equal to {n}.": "يجب أن تكون القيمة {n} أو أقل.",
    "Ensure this value is greater than or equal to {n}.": "يجب أن تكون القيمة {n} أو أكثر.",
    "\"{value}\" is not a valid choice.": "القيمة \"{value}\" غير صالحة.",
    "{model} with this {field} already exists.": "هذه القيمة مستخدمة بالفعل.",
    "Method \"{method}\" not allowed.": "هذا الإجراء غير متاح.",
    "Request was throttled. Expected available in {seconds} seconds.": "طلبات كثيرة جدًا. يرجى المحاولة بعد {seconds} ثانية.",
    "No {model} matches the given query.": "العنصر المطلوب غير موجود.",
    "Please wait {seconds}s before requesting a new code": "يرجى الانتظار {seconds} ثانية قبل طلب رمز جديد",
}

# Long messages written over several source lines: matched by their start.
PREFIXES = {
    "A candidate with this passport ID already exists under a different": "يوجد مرشح برقم جواز السفر هذا لدى حساب آخر.",
    "A candidate with this passport ID already has an issued certificate": "لدى مرشح برقم جواز السفر هذا شهادة صادرة بالفعل.",
    "Evaluation readiness corrections are immutable once generated": "لا يمكن تعديل تصحيحات الجاهزية بعد إنشائها.",
    "Your company account is restricted until its trade license is approved.": "حساب شركتك مقيّد حتى تتم الموافقة على الرخصة التجارية.",
    "Stripe error:": "تعذّر إكمال عملية الدفع. حاول مرة أخرى أو استخدم طريقة دفع أخرى.",
}

GENERIC_BY_STATUS = {
    400: "تعذّر إكمال الطلب. يرجى مراجعة البيانات والمحاولة مرة أخرى.",
    401: "يرجى تسجيل الدخول للمتابعة.",
    403: "لا تملك صلاحية تنفيذ هذا الإجراء.",
    404: "العنصر المطلوب غير موجود.",
    405: "هذا الإجراء غير متاح.",
    409: "تعذّر إكمال الطلب بسبب تعارض في البيانات.",
    413: "الملف كبير جدًا.",
    429: "طلبات كثيرة جدًا. يرجى الانتظار قليلًا ثم المحاولة مرة أخرى.",
}
GENERIC_SERVER = "حدث خطأ في الخادم. يرجى المحاولة مرة أخرى لاحقًا."

MESSAGE_KEYS = ("error", "detail", "message", "non_field_errors")


def _compile(template):
    names = []

    def repl(match):
        names.append(match.group(1))
        return "(?P<%s>.+?)" % match.group(1)

    pattern = re.sub(r"\\\{(\w+)\\\}", repl, re.escape(template))
    return re.compile("^" + pattern + "$"), names


_TEMPLATES = [(_compile(en)[0], ar) for en, ar in TEMPLATES.items()]
_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN_WORDS = re.compile(r"[A-Za-z]{3,}")


def translate_message(text, status_code=400):
    """Arabic for one API message, or None when it should stay as is."""
    if not isinstance(text, str) or not text.strip() or _ARABIC.search(text):
        return None
    stripped = text.strip()
    if stripped in EXACT:
        return EXACT[stripped]
    for pattern, arabic in _TEMPLATES:
        match = pattern.match(stripped)
        if match:
            return arabic.format(**match.groupdict())
    for prefix, arabic in PREFIXES.items():
        if stripped.startswith(prefix):
            return arabic
    if status_code >= 400 and _LATIN_WORDS.search(stripped):
        if status_code >= 500:
            return GENERIC_SERVER
        return GENERIC_BY_STATUS.get(status_code, GENERIC_BY_STATUS[400])
    return None


def _translate(value, status_code, top_level):
    if isinstance(value, str):
        return translate_message(value, status_code) or value
    if isinstance(value, list):
        return [_translate(v, status_code, False) for v in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            # At the top level only message fields are text for people;
            # field errors (lists of messages) are translated too.
            is_field_errors = status_code >= 400 and isinstance(item, list) and all(isinstance(i, str) for i in item)
            if top_level and key not in MESSAGE_KEYS and not is_field_errors:
                out[key] = item
                continue
            out[key] = _translate(item, status_code, False)
        return out
    return value


def wants_arabic(request):
    """The UI sends the page language as Accept-Language; the first
    language listed decides."""
    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    first = header.split(",")[0].strip().lower()
    return first.startswith("ar")


def translate_payload(data, status_code):
    return _translate(data, status_code, True)


class ArabicApiMessagesMiddleware:
    """For requests in Arabic, translates the API's English messages in
    JSON responses (errors and short confirmations). Field data is never
    touched: only message fields at the top level, and lists of field
    error messages."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not wants_arabic(request) or not request.path.startswith("/api/"):
            return response
        if "application/json" not in response.get("Content-Type", ""):
            return response
        if response.status_code < 400:
            data = getattr(response, "data", None)
            if not (isinstance(data, dict) and any(k in data for k in ("message", "detail"))):
                return response
        try:
            data = json.loads(response.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, AttributeError):
            return response
        if not isinstance(data, (dict, list)):
            return response
        translated = translate_payload(data, response.status_code)
        if translated == data:
            return response
        response.content = json.dumps(translated, ensure_ascii=False).encode("utf-8")
        if response.has_header("Content-Length"):
            response["Content-Length"] = str(len(response.content))
        return response
