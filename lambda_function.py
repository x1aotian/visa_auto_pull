from visa_monitor.monitor import run


def handler(event, context):
    return {"statusCode": 0 if run(dry_run=False) == 0 else 1}

