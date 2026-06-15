"""Logs the Care Gap Atlas planner agent to MLflow, registers it in Unity
Catalog, and deploys it to a Model Serving endpoint via Agent Framework.
"""
import os
import mlflow
from mlflow.models.resources import DatabricksServingEndpoint
from mlflow.types.responses import ResponsesAgentRequest

os.environ["DATABRICKS_CONFIG_PROFILE"] = "dbrx-hackathon-2026"
mlflow.set_tracking_uri("databricks")
mlflow.set_registry_uri("databricks-uc")

UC_MODEL_NAME = "workspace.default.care_gap_planner_agent"
SOURCE_ENDPOINT_NAME = "dbrxhack2026"

INPUT_EXAMPLE = {
    "input": [{"role": "user", "content": "Why does Bidar have a high care gap score for ICU access?"}]
}

if __name__ == "__main__":
    mlflow.set_experiment("/Shared/care_gap_atlas_planner_agent")

    with mlflow.start_run():
        logged = mlflow.pyfunc.log_model(
            python_model="agents/planner_agent.py",
            artifact_path="agent",
            code_paths=["agents"],
            input_example=INPUT_EXAMPLE,
            pip_requirements=[
                "mlflow",
                "databricks-sdk",
                "psycopg[binary]",
                "openai",
            ],
            resources=[
                DatabricksServingEndpoint(endpoint_name=SOURCE_ENDPOINT_NAME),
            ],
            registered_model_name=UC_MODEL_NAME,
        )

    print(f"Registered model: {UC_MODEL_NAME}, version {logged.registered_model_version}")
    print(f"Model URI: {logged.model_uri}")
