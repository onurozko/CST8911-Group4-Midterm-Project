
import json
import logging
import os
import uuid

import azure.functions as func
from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# /category as the partition key
CATEGORY = "foods"
FIELDS = {"name", "country", "type", "rating", "tried"}


def get_container():
    # Uses Azure CLI locally and Managed Identity when deployed.
    login = DefaultAzureCredential(
        exclude_interactive_browser_credential=True
    )

    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=login)
    database = client.get_database_client(os.environ["COSMOS_DATABASE"])
    return database.get_container_client(os.environ["COSMOS_CONTAINER"])

def reply(data, status=200):
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status,
        mimetype="application/json"
    )


def read_food(req):
    try:
        data = req.get_json()
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def check_food(data, creating=False):
    if not data:
        return "Please provide food information"

    if creating and ("name" not in data or "country" not in data):
        return "Name and country are required"

    for field, value in data.items():
        if field not in FIELDS:
            return f"Unknown field: {field}"

        if field in ("name", "country", "type"):
            if not isinstance(value, str) or not value.strip():
                return f"{field} must be text"
            if len(value.strip()) > 100:
                return f"{field} is too long"

        elif field == "rating":
            if type(value) is not int or not 1 <= value <= 10:
                return "Rating must be a number from 1 to 10"

        elif field == "tried":
            if not isinstance(value, bool):
                return "Tried must be true or false"

    return None


# List foods or add a new one
@app.route(route="foods", methods=["GET", "POST"])
def foods(req: func.HttpRequest):
    if req.method == "POST":
        data = read_food(req)
        error = check_food(data, creating=True)

        if error:
            return reply({"error": error}, 400)

        food = {
            "id": str(uuid.uuid4()),
            "category": CATEGORY,
            "name": data["name"].strip(),
            "country": data["country"].strip(),
            "tried": data.get("tried", False)
        }

        for field in ("type", "rating"):
            if field in data:
                food[field] = data[field]

        try:
            container = get_container()
            saved = container.create_item(body=food)
            return reply(saved, 201)
        except Exception:
            logging.exception("Failed to add food")
            return reply({"error": "Database request failed"}, 500)

    # GET /api/foods?country=Turkey&type=Main Dish
    query = "SELECT * FROM c WHERE c.category = @category"
    params = [{"name": "@category", "value": CATEGORY}]

    for field in ("country", "type"):
        value = req.params.get(field)
        if value:
            query += f" AND c.{field} = @{field}"
            params.append({"name": f"@{field}", "value": value})

    try:
        container = get_container()
        items = list(container.query_items(
            query=query,
            parameters=params,
            partition_key=CATEGORY
        ))
        return reply({"count": len(items), "foods": items})
    except Exception:
        logging.exception("Failed to list foods")
        return reply({"error": "Database request failed"}, 500)


# Read, edit or remove an existing food
@app.route(route="foods/{id}", methods=["GET", "PUT", "DELETE"])
def food_by_id(req: func.HttpRequest):
    food_id = req.route_params["id"]

    if req.method == "PUT":
        changes = read_food(req)
        error = check_food(changes)

        if error:
            return reply({"error": error}, 400)

    try:
        container = get_container()

        if req.method == "DELETE":
            container.delete_item(item=food_id, partition_key=CATEGORY)
            return reply({"message": "Food deleted", "id": food_id})

        food = container.read_item(item=food_id, partition_key=CATEGORY)

        if req.method == "GET":
            return reply(food)

        # Only update what the user actually sent
        for field, value in changes.items():
            if isinstance(value, str):
                value = value.strip()
            food[field] = value

        updated = container.replace_item(item=food_id, body=food)
        return reply(updated)

    except exceptions.CosmosResourceNotFoundError:
        return reply({"error": "Food not found"}, 404)
    except Exception:
        logging.exception("Food request failed")
        return reply({"error": "Database request failed"}, 500)
