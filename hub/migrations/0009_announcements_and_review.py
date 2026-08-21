import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0008_daily_update_blank_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='notifications_seen_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='taskupdate',
            name='reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='Announcement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=160)),
                ('body', models.TextField()),
                ('posted_at', models.DateTimeField(auto_now_add=True)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                             related_name='announcements', to='hub.employee')),
            ],
            options={'ordering': ['-posted_at']},
        ),
    ]
