# XML Manipulator
The XML Manipulator library updates XML files by adding and commenting XML tags.

# Installation
## Wheel Generation
To generate a Wheel file out of this library follow these steps:
1. Install these packages  
```bash
pip install setuptools
pip install wheel
```
2. Build the python wheel  
```bash
python3 setup.py bdist_wheel
```
3. Install the wheel  
```bash
pip install --no-cache-dir dist/xmlmaniplib-0.1.1-py3-none-any.whl
```

# Usage
```python
from xmlmaniplib.xml_manipulator import XMLManipulator

xml_manipulator = XMLManipulator()

# To modify an XML file, call one of the following functions:

xml_manipulator.update_xml(
    "path to XML file",
    ["<tags to comment as list of strings>"], 
    ["<tags to add as a list of strings>"]
)

xml_manipulator.update_xml_with_template("<filepath of a template file>")

```

### Example API function calls
```python
xml_manipulator.update_xml(
    "/a/etc/ghost/ghost.xml", 
    ["\<cache:uvm.cache-size>400</cache:uvm.cache-size>"], 
    ["\<config:rollback.use-certificate-channel>on</config:rollback.use-certificate-channel>"]
)  

xml_manipulator.update_xml_with_template("/path/to/sample_template_file.py")
```
## Template File
When calling `update_xml_with_template()` function, a template file needs to be provided. This file contains information about the XML files to be modified and the XML tags to be commented/added. This file should be a Python dictionary.

### Format
```python
{
    "<full path of XML file>" : {
        "tags_to_comment" : [
            metadata tags to be commented out
        ],
        "tags_to_add" : [
            metadata tags to be added
        ],
    }
}
```
### Example Template File
```python
{   
    "/a/etc/ghost/baseline.data/baseline-hacks.xml" : {
        "tags_to_add" : [
            r"<config:rollback.use-certificate-channel>off</config:rollback.use-certificate-channel>",
        ]
    },
    
    "/a/etc/ghost/baseline.data/baseline-ighost.xml" : {
        "tags_to_comment" : [
            r"<match:region.attribute value='ighost-63.209.255.4/12 ighost-216.200.14.241 ighost-216.200.14.233'>",
        ]
    },
}
```
Note: replace the double quotes around xml attributes with **single quotes**.

# Testing
The file `test.py` includes unit tests for this library. To test the library, run the following:  
```bash
python3 test.py
```
### Sample output
```
test_multiple_tags (__main__.TestAddTagMethod) ... ok
test_simple_tag (__main__.TestAddTagMethod) ... ok
test_tag_with_attributes (__main__.TestAddTagMethod) ... ok
test_tag_with_multiple_attributes (__main__.TestAddTagMethod) ... ok
test_multiple_tags (__main__.TestCommentTagMethod) ... ok
test_simple_tag (__main__.TestCommentTagMethod) ... ok
test_tag_with_attribute (__main__.TestCommentTagMethod) ... ok
test_tag_with_multiple_attributes (__main__.TestCommentTagMethod) ... ok
test_tags_with_subtags (__main__.TestCommentTagMethod) ... ok
test_add_to_invalid_header_file (__main__.TestInvalidInputs) ... ok
test_empty_tags (__main__.TestInvalidInputs) ... ok
test_invalid_attribute_format (__main__.TestInvalidInputs) ... ok
test_invalid_tag_format (__main__.TestInvalidInputs) ... ok
test_tag_does_not_exist (__main__.TestInvalidInputs) ... ok
test_template_file (__main__.TestUpdateWithTemplateMethod) ... ok

----------------------------------------------------------------------
Ran 15 tests in 0.059s

OK
```

# Contact 
Please reach out with any feedback/suggestions to:

Ani Chaloyan - [achaloya@trident.com](mailto:achaloya@trident.com)
