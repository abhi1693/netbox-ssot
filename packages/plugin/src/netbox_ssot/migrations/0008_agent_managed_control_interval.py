from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def use_interactive_default(apps: object, schema_editor: object) -> None:
    collector_agent = apps.get_model("netbox_ssot", "CollectorAgent")
    collector_agent.objects.filter(control_interval_seconds=30).update(control_interval_seconds=5)


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_ssot", "0007_agent_control_interval"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collectoragent",
            name="control_interval_seconds",
            field=models.PositiveSmallIntegerField(
                default=5,
                validators=[MinValueValidator(2), MaxValueValidator(30)],
            ),
        ),
        migrations.AddField(
            model_name="collectoragent",
            name="reported_control_interval_seconds",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(use_interactive_default, migrations.RunPython.noop),
    ]
