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

class GlossCache(models.Model):
    # 使用文本的 MD5 哈希作为 Key，方便快速查找
    text_hash = models.CharField(max_length=64, unique=True, db_index=True)
    # 存储 Gemini 返回的 JSON 数据: [{"w": "word", "m": "meaning", "g": "grammar"}, ...]
    gloss_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'gloss_cache'
