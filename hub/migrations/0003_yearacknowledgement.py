from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0002_seed_2026"),
    ]

    operations = [
        migrations.CreateModel(
            name="YearAcknowledgement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False,
                                           verbose_name="ID")),
                ("acknowledged_at", models.DateTimeField(auto_now_add=True)),
                ("annual_percent", models.DecimalField(blank=True, decimal_places=1,
                                                       max_digits=5, null=True)),
                ("comment", models.TextField(
                    blank=True,
                    help_text="Optional — anything the analyst wants recorded")),
                ("employee", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="acknowledgements", to="hub.employee")),
                ("year", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="acknowledgements", to="hub.appraisalyear")),
            ],
            options={"ordering": ["-acknowledged_at"]},
        ),
        migrations.AddConstraint(
            model_name="yearacknowledgement",
            constraint=models.UniqueConstraint(fields=("employee", "year"),
                                               name="uniq_acknowledgement_per_year"),
        ),
    ]
