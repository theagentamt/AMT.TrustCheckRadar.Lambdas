FROM public.ecr.aws/lambda/python:3.13

ARG MODEL_SOURCE=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ARG MODEL_REVISION=b8ef00830037f9868450f778081ea683e900fe39

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir --requirement ${LAMBDA_TASK_ROOT}/requirements.txt
COPY download_model.py ${LAMBDA_TASK_ROOT}/download_model.py
RUN MODEL_SOURCE=${MODEL_SOURCE} MODEL_REVISION=${MODEL_REVISION} MODEL_PATH=/opt/model \
    python ${LAMBDA_TASK_ROOT}/download_model.py && rm ${LAMBDA_TASK_ROOT}/download_model.py

ENV MODEL_PATH=/opt/model TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1
COPY app.py config.py model.py service.py ${LAMBDA_TASK_ROOT}/
CMD ["app.lambda_handler"]
