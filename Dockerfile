# AlphaForge 交易引擎镜像。
# 只打包运行所需代码（src/ + tests/），config/ 通过卷挂载注入，密钥绝不进镜像。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ALPHAFORGE_ENV=/app/config/env \
    ALPHAFORGE_CONFIG=/app/config/config.yaml

WORKDIR /app

# 先装依赖（含 dev，用于 afctl test 的验收测试），再拷源码。
COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN pip install --no-cache-dir -e ".[dev]"

# 默认跑交易主循环；afctl test 会覆盖为 python -m pytest。
CMD ["alphaforge", "run"]
