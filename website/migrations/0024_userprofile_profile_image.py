# -*- coding: utf-8 -*-
# Generated by Django 1.9.6 on 2017-02-02 18:32
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0023_payment_txn_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='profile_image',
            field=models.ImageField(blank=True, null=True, upload_to=b'profile_images/', verbose_name=b'Profile Image'),
        ),
    ]
