"""Optional typed filters over the prepared corpus, not a chat intent/keyword parser."""
import json

from .models import COUNTRIES, ingredient
from .pipeline import database


def select(output, *, country=None, region=None, servings=None, max_ingredients=None, ingredients=(), historical=None, limit=100):
    if not (output/'rag'/'catalog.sqlite3').is_file():raise ValueError('Prepare the recipe corpus before querying its catalog.')
    clauses=['active=1','needs_review=0'];parameters=[]
    for field,value in [('country',COUNTRIES.get(str(country).upper(),country) if country else None),('region',region),
                        ('servings',servings),('historic',int(historical) if historical is not None else None)]:
        if value is not None:
            clauses.append(field+'=?');parameters.append(value)
            recipe_field={'country':'pais_origen','region':'region','servings':'raciones'}.get(field)
            if recipe_field:
                clauses.append("NOT EXISTS (SELECT 1 FROM json_each(recipes.data,'$.metadata_conflicts') AS c WHERE json_extract(c.value,'$.field')=?)")
                parameters.append(recipe_field)
    if max_ingredients is not None:
        clauses.append('ingredient_count IS NOT NULL AND ingredient_count<=?');parameters.append(max_ingredients)
    for value in ingredients:
        name=ingredient(value).ingrediente
        if not name:raise ValueError('Ingredient filters cannot be empty.')
        clauses.append("EXISTS (SELECT 1 FROM json_each(recipes.data,'$.ingredientes_normalizados') AS i WHERE instr(' '||json_extract(i.value,'$.ingrediente')||' ',' '||?||' ')>0)")
        parameters.append(name)
    where=' AND '.join(clauses)
    with database(output) as connection:
        count=connection.execute('SELECT COUNT(*) FROM recipes WHERE '+where,parameters).fetchone()[0]
        rows=connection.execute('SELECT data FROM recipes WHERE '+where+' ORDER BY title,recipe_id LIMIT ?',[*parameters,limit])
        return {'total':count,'records':[json.loads(row['data']) for row in rows],
                'note':'Unknown counts/servings/origins do not satisfy exact filters.'}
