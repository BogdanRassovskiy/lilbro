from django.db import models
from django.utils import timezone


class FirmyAgent(models.Model):
    ROLE_SEARCHER = "searcher"
    ROLE_EVALUATOR = "evaluator"
    ROLE_INTERVIEWER = "interviewer"

    name = models.CharField("имя", max_length=120)
    avatar = models.CharField("аватар", max_length=8, default="👤", blank=True)
    email = models.EmailField("email", blank=True)
    phone = models.CharField("номер телефона", max_length=40, blank=True)
    system_prompt = models.TextField("промпт (кто я и задача)", blank=True, default="")
    prompt_scope = models.TextField("промпт: разрешенные темы и вопросы", blank=True, default="")
    prompt_strategy = models.TextField("промпт: стратегия и тактика ответа", blank=True, default="")
    model_name = models.CharField("название модели", max_length=120, blank=True)
    token_limit = models.PositiveIntegerField("лимит токенов", default=0)
    processing_enabled = models.BooleanField("автопроцессинг включен", default=True)
    role = models.CharField(
        "роль",
        max_length=24,
        choices=(
            (ROLE_SEARCHER, "поисковик"),
            (ROLE_EVALUATOR, "оценщик"),
            (ROLE_INTERVIEWER, "собеседник"),
        ),
        default=ROLE_SEARCHER,
    )
    created_at = models.DateTimeField("создано", auto_now_add=True)
    updated_at = models.DateTimeField("обновлено", auto_now=True)

    class Meta:
        ordering = ("name", "id")
        verbose_name = "агент"
        verbose_name_plural = "агенты"

    def __str__(self):
        return "{} ({})".format(self.name, self.get_role_display())


class FirmySearchRun(models.Model):
    """Один запуск поиска на firmy.cz (метаданные)."""

    STATUS_PENDING = "pending"
    STATUS_OK = "ok"
    STATUS_ERROR = "error"

    query = models.CharField("запрос", max_length=500, db_index=True)
    expected_limit = models.PositiveSmallIntegerField("ожидаемый максимум результатов", default=10)
    search_url = models.URLField("URL поиска", max_length=2048)
    created_at = models.DateTimeField("создано", auto_now_add=True)
    finished_at = models.DateTimeField("завершено", null=True, blank=True)
    status = models.CharField(
        "статус",
        max_length=16,
        choices=(
            (STATUS_PENDING, "в процессе"),
            (STATUS_OK, "успех"),
            (STATUS_ERROR, "ошибка"),
        ),
        default=STATUS_PENDING,
        db_index=True,
    )
    error_message = models.TextField("текст ошибки", blank=True)
    results_count = models.PositiveIntegerField("сохранено записей", default=0)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "запуск поиска Firmy"
        verbose_name_plural = "запуски поиска Firmy"

    def __str__(self):
        return "{} @ {}".format(self.query, self.created_at)


class FirmyPremise(models.Model):
    """Уникальная карточка компании (dedup по premise_id)."""

    premise_id = models.PositiveIntegerField("premise id", unique=True, db_index=True)
    title = models.CharField("название", max_length=500)
    detail_url = models.URLField("ссылка на профиль", max_length=2048)
    category = models.CharField("категория", max_length=300, blank=True)
    address = models.CharField("адрес", max_length=500, blank=True)
    card_text = models.TextField("текст карточки (сырой)", blank=True)
    phones = models.TextField(
        "телефоны",
        blank=True,
        help_text="нормализованные номера, по одному на строку",
    )
    emails = models.TextField(
        "e-mail",
        blank=True,
        help_text="по одному на строку",
    )
    website_url = models.URLField(
        "сайт",
        max_length=2048,
        blank=True,
        help_text="ссылка с профиля (часто кнопка «Web» — может вести на соцсеть)",
    )
    first_seen_at = models.DateTimeField("впервые замечено", auto_now_add=True)
    updated_at = models.DateTimeField("обновлено", auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        verbose_name = "карточка Firmy"
        verbose_name_plural = "карточки Firmy"

    def __str__(self):
        return "{} — {}".format(self.premise_id, self.title)


class FirmySearchHit(models.Model):
    """Результат конкретного запуска: позиция + ссылка на уникальную карточку."""

    run = models.ForeignKey(
        FirmySearchRun,
        related_name="hits",
        on_delete=models.CASCADE,
        verbose_name="запуск",
    )
    position = models.PositiveSmallIntegerField("позиция в выдаче", db_index=True)
    premise = models.ForeignKey(
        FirmyPremise,
        related_name="hits",
        on_delete=models.CASCADE,
        verbose_name="карточка",
    )

    class Meta:
        ordering = ("run", "position")
        unique_together = (("run", "position"), ("run", "premise"))
        verbose_name = "попадание Firmy"
        verbose_name_plural = "попадания Firmy"

    def __str__(self):
        return "run={} pos={} premise={}".format(self.run_id, self.position, self.premise_id)


class FirmyProcessingItem(models.Model):
    """Элемент очереди обработки (контакт/ответ/переписка) для карточки."""

    premise = models.ForeignKey(
        FirmyPremise,
        related_name="processing_items",
        on_delete=models.CASCADE,
        verbose_name="карточка",
    )
    assigned_agent = models.ForeignKey(
        FirmyAgent,
        related_name="processing_items",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="закрепленный собеседник",
    )
    queued_at = models.DateTimeField("в очереди с", db_index=True, default=timezone.now)
    created_at = models.DateTimeField("добавлено в обработку", auto_now_add=True)
    updated_at = models.DateTimeField("обновлено", auto_now=True)
    was_contacted = models.BooleanField("был ли контакт", default=False)
    was_answered = models.BooleanField("был ли ответ", default=False)
    lead_state = models.CharField("состояние лида", max_length=16, blank=True, default="")
    response_type = models.TextField(
        "тип ответа (JSON массив)",
        blank=True,
        default="[]",
        help_text='JSON-массив, например: ["no_response","interested"]',
    )
    communication_style = models.TextField(
        "стиль коммуникации (JSON массив)",
        blank=True,
        default="[]",
        help_text='JSON-массив, например: ["short","formal"]',
    )
    reply_delay_min_minutes = models.PositiveSmallIntegerField("минимальная задержка ответа (мин)", default=0)
    reply_delay_max_minutes = models.PositiveSmallIntegerField("максимальная задержка ответа (мин)", default=0)
    paused_individual = models.BooleanField("чат на паузе индивидуально", default=False)
    auto_reply_send_immediate = models.BooleanField(
        "автоответ сразу в чат (без черновика)",
        default=False,
        help_text="Если да — при входящих и фоновом автоответе текст сразу в переписке; если нет — черновик с подтверждением.",
    )
    do_not_contact = models.BooleanField("больше не писать", default=False)
    draft_text = models.TextField("черновик сообщения", blank=True, default="")
    draft_requires_confirmation = models.BooleanField("черновик требует подтверждения", default=False)
    GEN_IDLE = "idle"
    GEN_RUNNING = "running"
    GEN_DONE = "done"
    GEN_ERROR = "error"
    gen_status = models.CharField(
        "статус генерации",
        max_length=16,
        choices=(
            (GEN_IDLE, "не запущено"),
            (GEN_RUNNING, "в процессе"),
            (GEN_DONE, "готово"),
            (GEN_ERROR, "ошибка"),
        ),
        default=GEN_IDLE,
        db_index=True,
    )
    gen_started_at = models.DateTimeField("генерация начата", null=True, blank=True)
    gen_finished_at = models.DateTimeField("генерация завершена", null=True, blank=True)
    gen_error = models.TextField("ошибка генерации", blank=True, default="")
    EVAL_IDLE = "idle"
    EVAL_RUNNING = "running"
    EVAL_DONE = "done"
    EVAL_ERROR = "error"
    evaluation_text = models.TextField("оценка (текст)", blank=True, default="")
    eval_status = models.CharField(
        "статус оценки",
        max_length=16,
        choices=(
            (EVAL_IDLE, "не запущено"),
            (EVAL_RUNNING, "в процессе"),
            (EVAL_DONE, "готово"),
            (EVAL_ERROR, "ошибка"),
        ),
        default=EVAL_IDLE,
        db_index=True,
    )
    eval_started_at = models.DateTimeField("оценка начата", null=True, blank=True)
    eval_finished_at = models.DateTimeField("оценка завершена", null=True, blank=True)
    eval_stage = models.CharField("этап оценки", max_length=32, blank=True, default="")
    eval_error = models.TextField("ошибка оценки", blank=True, default="")
    REPLY_IDLE = "idle"
    REPLY_RUNNING = "running"
    REPLY_DONE = "done"
    REPLY_ERROR = "error"
    reply_status = models.CharField(
        "статус автоответа",
        max_length=16,
        choices=(
            (REPLY_IDLE, "не запущено"),
            (REPLY_RUNNING, "в процессе"),
            (REPLY_DONE, "готово"),
            (REPLY_ERROR, "ошибка"),
        ),
        default=REPLY_IDLE,
        db_index=True,
    )
    reply_started_at = models.DateTimeField("автоответ начат", null=True, blank=True)
    reply_finished_at = models.DateTimeField("автоответ завершен", null=True, blank=True)
    reply_error = models.TextField("ошибка автоответа", blank=True, default="")
    conversation_json = models.TextField(
        "переписка (JSON массив)",
        blank=True,
        default="[]",
        help_text='JSON-массив сообщений, например: [{"ts":"2026-03-26T12:00:00Z","dir":"out","text":"..."}]',
    )

    class Meta:
        ordering = ("queued_at", "id")
        unique_together = (("premise", "assigned_agent"),)
        verbose_name = "обработка Firmy"
        verbose_name_plural = "обработка Firmy"

    def __str__(self):
        return "processing: premise={} agent={}".format(self.premise_id, self.assigned_agent_id)


class FirmySearchResult(models.Model):
    """Legacy: старая схема (результаты хранились отдельно для каждого run)."""

    run = models.ForeignKey(
        FirmySearchRun,
        related_name="results",
        on_delete=models.CASCADE,
        verbose_name="запуск",
    )
    position = models.PositiveSmallIntegerField("позиция в выдаче", db_index=True)
    premise_id = models.PositiveIntegerField("premise id", db_index=True)
    title = models.CharField("название", max_length=500)
    detail_url = models.URLField("ссылка на профиль", max_length=2048)
    category = models.CharField("категория", max_length=300, blank=True)
    address = models.CharField("адрес", max_length=500, blank=True)
    card_text = models.TextField("текст карточки (сырой)", blank=True)
    phones = models.TextField(
        "телефоны",
        blank=True,
        help_text="нормализованные номера, по одному на строку",
    )
    emails = models.TextField(
        "e-mail",
        blank=True,
        help_text="по одному на строку",
    )
    website_url = models.URLField(
        "сайт",
        max_length=2048,
        blank=True,
        help_text="ссылка с профиля (часто кнопка «Web» — может вести на соцсеть)",
    )

    class Meta:
        ordering = ("run", "position")
        unique_together = (("run", "position"), ("run", "premise_id"))
        verbose_name = "результат Firmy"
        verbose_name_plural = "результаты Firmy"

    def __str__(self):
        return "{} — {}".format(self.premise_id, self.title)
