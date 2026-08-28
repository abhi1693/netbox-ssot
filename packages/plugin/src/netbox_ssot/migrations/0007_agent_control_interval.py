from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_ssot", "0006_agent_health_commands"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectoragent",
            name="control_interval_seconds",
            field=models.PositiveSmallIntegerField(default=30),
        ),
    ]
