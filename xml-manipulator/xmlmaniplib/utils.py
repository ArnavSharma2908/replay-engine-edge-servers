import re
import logging
from enum import IntEnum

from xml.dom import minidom
from xmlmaniplib.hasher import HASH_COMMENT_PREFIX


class DuplicateHashAction(IntEnum):
    """Enum for duplicate content hash action behavior."""
    SKIP = 0   # skip adding the tags entirely (silent, no comment, no tags written)
    WARN = 1   # add tags with a warning comment (default)

log = logging.getLogger(__name__)

def is_attribute_match(elem, attr, value):
    return elem.hasAttribute(attr) and elem.getAttribute(attr) == value

def get_tag_name(tag):
    if ' ' in tag:
        return tag[0:tag.index(' ')]
    return tag

def get_closing_quote(tag):
    first_quote = tag.find('\'')
    if first_quote == -1 or first_quote >= len(tag)-2:
        return -1

    second_quote = tag[first_quote+1:].find('\'')
    if second_quote == -1:
        return -1

    return first_quote + second_quote + 1

def get_attributes(tag):

    tag = tag.strip()
    attributes = {}
    closing_quote_index = get_closing_quote(tag)
    while closing_quote_index != -1:
        attr = re.match(r'(\S+)=\'([^\']+)\'', tag[0:closing_quote_index+1])
        if attr == None or attr.group(1)==None or attr.group(2)==None:
            return -1

        name = attr.group(1)
        value = attr.group(2)
        attributes[name] = value

        if closing_quote_index >= len(tag)-2:
            break

        tag = tag[closing_quote_index+1:]
        tag = tag.lstrip()
        closing_quote_index = get_closing_quote(tag)


    if closing_quote_index == -1:
        return -1

    return attributes

def add_element_with_comment(xml_doc: minidom.Document, current_element: minidom.Element, tag_name: str, attributes: str = '', open_tags: list = None, manipulator: 'XMLManipulator' = None, tag_value = None) -> minidom.Element:
    """Add an element with a comment to the XML document."""
    new_element = xml_doc.createElement(tag_name)
    
    if attributes:
        attr_expr = re.compile(r'(\w+)=["\']([^"\']+)["\']')
        for match in attr_expr.finditer(attributes):
            attr_name, attr_value = match.groups()
            new_element.setAttribute(attr_name, attr_value)
    
    if tag_value is not None:
        new_element.appendChild(xml_doc.createTextNode(tag_value))

    if manipulator and not manipulator.comment_added:
        root = xml_doc.documentElement
        elem_list = root.getElementsByTagName("trident:edge-config") or root.getElementsByTagName("edge-config")
        if elem_list:
            parent_element = elem_list.item(0)
            parent_element.appendChild(xml_doc.createTextNode("\n"))
            comment = xml_doc.createComment("Added by XML Manipulator library")
            parent_element.appendChild(comment)
            if getattr(manipulator, 'tags_hash', None):
                add_newline_and_indent(xml_doc, parent_element, open_tags)
                hash_comment = xml_doc.createComment(HASH_COMMENT_PREFIX + manipulator.tags_hash)
                parent_element.appendChild(hash_comment)
                if getattr(manipulator, 'hash_duplicate', False):
                    add_newline_and_indent(xml_doc, parent_element, open_tags)
                    warn_comment = xml_doc.createComment(
                        "Warning: An identical content hash already exists in this file. "
                        "Adding the tags anyway."
                    )
                    parent_element.appendChild(warn_comment)
            add_newline_and_indent(xml_doc, parent_element, open_tags)
            manipulator.comment_added = True
        else:
            log.error("Unable to add tag to the provided file because the format is not supported.")
            return None

    if current_element is not None:
        add_newline_and_indent(xml_doc, current_element, open_tags, decrease_indent=False)
        current_element.appendChild(new_element)
    else:
        root = xml_doc.documentElement
        elem_list = root.getElementsByTagName("trident:edge-config") or root.getElementsByTagName("edge-config")
        if len(elem_list) > 0:
            parent_element = elem_list.item(0)
            parent_element.appendChild(new_element)
            add_newline_and_indent(xml_doc, parent_element, open_tags)
        else:
            log.error("Unable to add tag to the provided file because the format is not supported.")
            return None
    
    return new_element

def add_tag_with_value(tag_name: str, tag_value: str, xml_doc: minidom.Document, current_element: minidom.Element, attributes, open_tags: list, manipulator: 'XMLManipulator'):
    """Add a tag with value to the XML document."""
    add_element_with_comment(xml_doc, current_element, tag_name, attributes, open_tags, manipulator, tag_value)

def add_opening_tag(tag_name: str, xml_doc: minidom.Document, current_element: minidom.Element, attributes, open_tags: list, manipulator: 'XMLManipulator'):
    """Add an opening tag to the XML document."""
    new_element = add_element_with_comment(xml_doc, current_element, tag_name, attributes, open_tags=open_tags, manipulator=manipulator)
    current_element = new_element
    open_tags.append(tag_name)
    return new_element

def add_closing_tag(tag_name: str, xml_doc: minidom.Document, current_element: minidom.Element, open_tags: list, manipulator: 'XMLManipulator'):
    """
    Handle a closing tag by reverting to the parent element and adding a newline if necessary.
    
    :param tag_name: The name of the tag
    :param xml_doc: The XML document to which the tag will be added
    :param current_element: The current element being processed
    :param open_tags: The list of open tags to manage indentation
    :param manipulator: The XMLManipulator instance
    """
    if current_element is not None and open_tags and open_tags[-1] == tag_name:
        # Decrease indentation before closing the tag
        add_newline_and_indent(xml_doc, current_element, open_tags, decrease_indent=True)
        if manipulator and not manipulator.comment_added:
            comment = xml_doc.createComment("Added by XML Manipulator library")
            current_element.appendChild(comment)
            if getattr(manipulator, 'tags_hash', None):
                add_newline_and_indent(xml_doc, current_element, open_tags)
                hash_comment = xml_doc.createComment(HASH_COMMENT_PREFIX + manipulator.tags_hash)
                current_element.appendChild(hash_comment)
                if getattr(manipulator, 'hash_duplicate', False):
                    add_newline_and_indent(xml_doc, current_element, open_tags)
                    warn_comment = xml_doc.createComment(
                        "Warning: An identical content hash already exists in this file. "
                        "Adding the tags anyway."
                    )
                    current_element.appendChild(warn_comment)
            manipulator.comment_added = True
        current_element = current_element.parentNode
        open_tags.pop()
    else:
        log.error("Mismatched or invalid closing tag: \"" + tag_name + "\".")
    return current_element

def add_newline_and_indent(xml_doc: minidom.Document, current_element, open_tags, decrease_indent=False):
    """
    Add a newline and indentation to the XML document.
    
    :param xml_doc: The XML document to be formatted
    :param current_element: The current element to which newline and indent will be added
    :param open_tags: The stack of open tags to determine the indentation level
    :param decrease_indent: Flag to decrease indentation level before adding newline
    """
    if current_element is not None:
        indent_level = len(open_tags) + 1
        if decrease_indent:
            indent_level -= 1
        new_line = xml_doc.createTextNode("\n" + "  " * indent_level)
        current_element.appendChild(new_line)

def validate_attributes(attributes: str):
    # Regex to match valid attribute format (name="value" or name='value')
    attribute_expr = re.compile(r'([a-zA-Z_:][\w:.-]*)\s*=\s*(["\'])(.*?)\2')
    
    # Extract valid attributes and validate each
    for attr in re.finditer(attribute_expr, attributes):
        attr_name = attr.group(1)
        attr_value = attr.group(3)
        
        # Ensure attribute names follow the XML rules
        if not re.match(r'^[a-zA-Z_:][\w:.-]*$', attr_name):
            return False
        
        # Ensure the attribute value is not empty
        if attr_value == '':
            return False
        
    # Ensure no malformed attributes exist after extracting the valid ones
    invalid_attr_part = re.sub(attribute_expr, '', attributes).strip()
    if invalid_attr_part:
        return False
    
    return True

def validate_tag_structure(tag: str) -> bool:
    """Validate the structure of an XML-like tag."""
    # Regular expressions for opening, closing, and tags with value
    tag_with_value_expr = re.compile(r'<([a-zA-Z0-9:._-]+)\s*[^>]*>(.*?)</\1>', re.DOTALL)
    tag_open_expr = re.compile(r'^<([a-zA-Z0-9:._-]+)(?![^>]*<\/)[^>]*>$')
    tag_close_expr = re.compile(r'</([a-zA-Z0-9:._-]+)>')

    # Check for an empty or invalid tag
    if not tag.startswith('<') or '>' not in tag:
        return False

    # Handle case of completely empty tags like "<>"
    if '<>' in tag:
        return False

    # Ensure the tag has a closing '>'
    if '<' in tag and '>' not in tag:
        return False

    # Check if it's a tag with value content
    match_value_tag = tag_with_value_expr.match(tag)
    if match_value_tag:
        tag_name = match_value_tag.group(1)
        content = match_value_tag.group(2)
        if content.strip() == '':  # No content between opening and closing tags
            return False
        return True

    # Check for opening tags
    match_open = tag_open_expr.match(tag)
    if match_open:
        # Check that the tag is properly closed
        if not tag.endswith('>'):
            return False
        return True

    # Check for valid closing tag
    if tag_close_expr.match(tag):
        return True
    return False