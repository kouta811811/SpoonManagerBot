# Worker（ECS Fargate）と Lambda（OAuth callback・Control）で同じイメージを使う。
# ベースはLambdaのPython 3.12（arm64、boto3同梱）。秘密はイメージに入れない。
FROM public.ecr.aws/lambda/python:3.12-arm64

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/var/task

COPY src/spoon_manager_bot ${LAMBDA_TASK_ROOT}/spoon_manager_bot

# Lambdaの既定ハンドラー。Lambda側は ImageConfig、Worker側は entryPoint で上書きする。
CMD ["spoon_manager_bot.oauth_callback.handler"]
