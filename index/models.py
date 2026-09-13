# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class Contents(models.Model):
    work_id = models.IntegerField(blank=True, null=True)
    text = models.TextField(blank=True, null=True)
    subtype = models.TextField(blank=True, null=True)
    div_type = models.TextField(blank=True, null=True)
    n_value = models.TextField(blank=True, null=True)
    path = models.TextField(blank=True, null=True)
    global_order = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'contents'


class Works(models.Model):
    author = models.TextField(blank=True, null=True)
    title = models.TextField(blank=True, null=True)
    urn = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'works'

class DictEntry(models.Model):
    """公开词典条目：逐词标注的主力数据源，一次收录永久复用。"""

    word_form = models.CharField(max_length=64, unique=True, db_index=True)
    lemma = models.CharField(max_length=64, blank=True, default='')
    pos = models.CharField(max_length=64, blank=True, default='')
    morph = models.CharField(max_length=255, blank=True, default='')  # 格/数/时态等
    gloss_en = models.TextField(blank=True, default='')
    gloss_zh = models.TextField(blank=True, default='')  # 由离线脚本用 AI 批量补
    source = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dict_entry'

    def __str__(self):
        return f"{self.word_form} -> {self.lemma or self.word_form}"


class ChapterNote(models.Model):
    """AI 讲解：整章一次生成，永久缓存。"""

    work_id = models.IntegerField()
    path = models.TextField()
    text_hash = models.CharField(max_length=64, db_index=True)
    note = models.TextField()
    provider = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chapter_note'
        unique_together = ('work_id', 'text_hash')


class GlossCache(models.Model):
    # 使用文本的 MD5 哈希作为 Key，方便快速查找
    text_hash = models.CharField(max_length=64, unique=True, db_index=True)
    # 存储 Gemini 返回的 JSON 数据: [{"w": "word", "m": "meaning", "g": "grammar"}, ...]
    gloss_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'gloss_cache'
