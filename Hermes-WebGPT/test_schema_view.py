import copy
import unittest
from model_prompt_compaction import _schema_view
from hermes_tool_schema_guard import validate_offered_tool_arguments


class SchemaViewTests(unittest.TestCase):
    def test_parameter_names_that_resemble_annotations_survive(self):
        names=['title','description','examples','example','deprecated','$comment']
        schema={'type':'object','properties':{name:{'type':'string','description':'prose'} for name in names},'required':names}
        view=_schema_view(schema)
        self.assertEqual(set(view['properties']),set(names))
        self.assertEqual(view['required'],names)

    def test_nested_parameter_names_survive(self):
        schema={'properties':{'nested':{'properties':{'description':{'type':'string'}}}}}
        self.assertIn('description',_schema_view(schema)['properties']['nested']['properties'])

    def test_enum_literal_objects_are_exact(self):
        values=[{'title':'literal','description':{'examples':[1,2]}}]
        self.assertEqual(_schema_view({'enum':values})['enum'],values)

    def test_const_literal_objects_are_exact(self):
        value={'description':'must stay','title':{'example':False}}
        self.assertEqual(_schema_view({'const':value})['const'],value)

    def test_default_literal_objects_are_exact(self):
        value={'description':'fallback','examples':['x']}
        self.assertEqual(_schema_view({'default':value})['default'],value)

    def test_definition_names_and_refs_survive(self):
        schema={'$defs':{'description':{'type':'string'}},'properties':{'title':{'$ref':'#/$defs/description'}}}
        view=_schema_view(schema)
        self.assertEqual(view,schema)
        validate_offered_tool_arguments([{'function':{'name':'fixture','parameters':view}}],'fixture',{'title':'ok'})

    def test_pattern_names_are_not_annotations(self):
        schema={'patternProperties':{'description':{'type':'string'},'title':{'type':'number'}}}
        self.assertEqual(_schema_view(schema),schema)

    def test_schema_arrays_trim_annotations_and_keep_literals(self):
        schema={'allOf':[{'description':'remove','properties':{'title':{'const':{'description':'literal'}}}}]}
        view=_schema_view(schema)
        self.assertNotIn('description',view['allOf'][0])
        self.assertEqual(view['allOf'][0]['properties']['title']['const'],{'description':'literal'})

    def test_items_and_boolean_schemas_survive(self):
        schema={'items':[{'description':'remove','type':'string'},False], 'additionalProperties':False}
        view=_schema_view(schema)
        self.assertEqual(view['items'],[{'type':'string'},False])
        self.assertIs(view['additionalProperties'],False)

    def test_legacy_dependencies_keep_names_and_data(self):
        schema={'dependencies':{'description':['title'],'title':{'description':'prose','required':['description']}}}
        view=_schema_view(schema)
        self.assertEqual(view['dependencies']['description'],['title'])
        self.assertEqual(view['dependencies']['title'],{'required':['description']})

    def test_unknown_keyword_values_are_opaque(self):
        value={'description':'unknown semantics','title':[{'examples':[1]}]}
        self.assertEqual(_schema_view({'x-extension':value})['x-extension'],value)

    def test_focused_description_does_not_replace_a_literal_hint(self):
        schema={'description':'A'*200,'hint':{'title':'opaque'}}
        view=_schema_view(schema,detailed=True)
        self.assertEqual(view['hint'],schema['hint'])
        self.assertLess(len(view['description']),60)

    def test_view_mutation_cannot_change_literal_canonical_data(self):
        schema={'enum':[{'description':['original']}]}
        before=copy.deepcopy(schema)
        view=_schema_view(schema)
        view['enum'][0]['description'].append('changed')
        self.assertEqual(schema,before)


if __name__=='__main__':unittest.main()
