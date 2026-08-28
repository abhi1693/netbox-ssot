from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_ssot", "0008_agent_managed_control_interval"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="agentcommand",
            name="ssot_command_source_kind_active_uniq",
        ),
        migrations.AddField(
            model_name="agentcommand",
            name="last_progress_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentcommand",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentcommand",
            name="reporting_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="agentcommand",
            name="state",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("dispatched", "Dispatched"),
                    ("running", "Running"),
                    ("reporting", "Reporting"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="agentcommand",
            constraint=models.UniqueConstraint(
                condition=models.Q(state__in=("pending", "dispatched", "running", "reporting")),
                fields=("source", "kind"),
                name="ssot_command_source_kind_active_uniq",
            ),
        ),
    ]
