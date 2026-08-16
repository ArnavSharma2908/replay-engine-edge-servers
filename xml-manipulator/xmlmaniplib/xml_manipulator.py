from shutil import copyfile
from xml.dom import minidom
from typing import List
from xmlmaniplib.utils import *
from xmlmaniplib.hasher import compute_tags_hash, find_hash_in_comments

import ast
import os
import stat
import logging


log = logging.getLogger(__name__)

class XMLManipulator():
    """
    Class which contains the API functions to manipulate XML.
    """
    # Controls behaviour when a duplicate content hash is detected in the target file.
    DUPLICATE_HASH_ACTION = DuplicateHashAction.SKIP  # Default: skip adding tags silently

    def __init__(self):
        self.current_element = None  # Keep track of the current element
        self.open_tags = [] #Stack to keep track of open tags
        self.comment_added = False # Track if the comment has been added
        self.tags_hash = None      # MD5 hash of the current batch of tags_to_add
        self.hash_duplicate = False # True if an identical hash already exists in the file
    
    def add_tag(self, tag: str, xml_doc: minidom.Document):
        """
        Add a tag to the XML document.
        
        :param tag: The tag to be added (e.g., '<parent>Value</parent>')
        :param xml_doc: The XML document to which the tag will be added
        """
        is_valid_tag = validate_tag_structure(tag)

        if not is_valid_tag:
            log.error("Tag \""+tag+"\" is in incorrect format (add tag).")
            return
    
        # Define tag patterns
        tag_open_simple_expr = re.compile(r'<([^\s/>]+)([^>]*)>')
        tag_close_expr = re.compile(r'</([^\s>]+)>')
        tag_with_value_expr = re.compile(r'<([^\s/>]+)([^>]*)>(.*?)</\1>', re.DOTALL)

        if tag_with_value_expr.match(tag):
            # Tag with value
            match = tag_with_value_expr.match(tag)
            tag_name = match.group(1)
            attributes = match.group(2)
            tag_value = match.group(3)
            is_valid_attribute = validate_attributes(attributes)
            if not is_valid_attribute:
                log.error("Attributes in tag \""+tag+"\" are in incorrect format (add tag).")
            add_tag_with_value(tag_name, tag_value, xml_doc, self.current_element, attributes, self.open_tags, self)
        elif tag_open_simple_expr.match(tag):
            # Opening tag
            tag_name = tag_open_simple_expr.match(tag).group(1)
            attributes = tag_open_simple_expr.match(tag).group(2)
            is_valid_attribute = validate_attributes(attributes)
            if not is_valid_attribute:
                log.error("Attributes in tag \""+tag+"\" are in incorrect format (add tag).")
            self.current_element = add_opening_tag(tag_name, xml_doc, self.current_element, attributes, self.open_tags, self)
        elif tag_close_expr.match(tag):
            # Closing tag
            tag_name = tag_close_expr.match(tag).group(1)
            self.current_element = add_closing_tag(tag_name, xml_doc, self.current_element, self.open_tags, self)
        else:
            log.error("Tag \""+tag+"\" is in incorrect format (add tag).")

    def comment_tag(self, tag: str, xml_doc: minidom.Document):
        """Comment a tag in an XML document

        Args:
            tag: XML tag string to add
            xml_doc: XML Document object
        """
        # get tag info
        expr = re.match(r'<([^>]+)>([^<]*)', tag)
        if expr == None or expr.group(1) == None:
            log.error("Tag \""+tag+"\" is in incorrect format (comment tag).")
            return

        tag_info = expr.group(1)                # contains tag name and attributes (if any present)
        tag_text = expr.group(2)
        tag_name = get_tag_name(tag_info)
        attrs = None
        if tag_info.find(' ') != -1:
            attrs = get_attributes(tag_info[tag_info.index(' '):])
        if attrs == -1:
            log.error("Attributes in tag \""+tag+"\" are in incorrect format (comment tag).")
            return

        # comment out tag
        comments = xml_doc.documentElement.getElementsByTagName(tag_name)
        if len(comments) == 0:
            log.warning("Attempting to comment tag \""+tag_name+"\" that does not exist in file.")
            return

        match_found = False
        for elem in comments:
            # check if tag text matches
            if len(tag_text) > 0 and elem.firstChild.nodeValue != tag_text:
                continue

            # check if attributes match
            is_match = True
            if attrs:
                for name,value in attrs.items():
                    if not is_attribute_match(elem, name, value):
                        is_match = False
                        break

            if is_match:
                parent_node = elem.parentNode
                # Replace all double hyphens to avoid XML comment validation errors
                element_xml = elem.toxml()
                while '--' in element_xml:
                    element_xml = element_xml.replace('--', '- -')
                commentText  = xml_doc.createComment(element_xml)
                parent_node.replaceChild(commentText, elem)
                match_found = True

        if not match_found:
            log.warning("Tag \""+tag+"\" with specified attributes/text was not found.")

    def comment_tags_by_pattern(self, tag_pattern: str, xml_doc: minidom.Document) -> int:
        """
        Comment all tags matching a pattern (prefix) in an XML document.

        Args:
            tag_pattern: Tag name pattern/prefix to match (e.g., 'security:ssl-forward-cert-verify-2')
            xml_doc: XML Document object

        Returns:
            Number of tags commented
        """
        commented_count = 0

        # Get all elements in the document
        all_elements = xml_doc.getElementsByTagName('*')

        # Find all matching elements
        elements_to_comment = []
        for elem in all_elements:
            tag_name = elem.tagName
            # Check if tag name starts with the pattern
            if tag_name.startswith(tag_pattern):
                elements_to_comment.append(elem)

        # Comment each matching element
        for elem in elements_to_comment:
            try:
                parent_node = elem.parentNode
                # Convert element to XML string and create comment
                # Replace -- with - - to avoid invalid XML comments
                # Need to replace all occurrences including in nested content
                element_xml = elem.toxml()

                # Replace all double hyphens with spaced hyphens
                # This includes certificate delimiters like -----BEGIN/END-----
                while '--' in element_xml:
                    element_xml = element_xml.replace('--', '- -')

                # Log the tag being commented with preview of content
                # Truncate long content for readability
                preview = element_xml[:200] + "..." if len(element_xml) > 200 else element_xml
                log.info(f"Commenting tag: {elem.tagName}")
                log.info(f"  Content preview: {preview}")

                comment_node = xml_doc.createComment(element_xml)
                parent_node.replaceChild(comment_node, elem)
                commented_count += 1
            except Exception as e:
                log.error(f"Failed to comment tag {elem.tagName}: {e}")

        if commented_count == 0:
            log.warning(f"No tags found matching pattern: {tag_pattern}")
        else:
            log.info(f"Commented {commented_count} tag(s) matching pattern: {tag_pattern}")

        return commented_count

    def update_xml(self, base_xml_file: str, tags_to_comment: List[str]=[], tags_to_add: List[str]=[]):
        """Update an XML file with tags to add and tags to comment and write it to this file.

        Args:
            base_xml_file: Path to the base XML file to which tags will be added or commented
            tags_to_comment: A list of XML tags to comment
            tags_to_add: A list of XML tags to add
        """
        # BugFix: Reset instance state flags to allow reuse of XMLManipulator across multiple files
        self.comment_added = False
        self.tags_hash = None
        self.hash_duplicate = False
        
        if not tags_to_add and not tags_to_comment:
            log.error("Should specify either tags to add or tags to comment.")
            return

        # parse xml file into a minidom.Document
        xml_doc = minidom.parse(base_xml_file)

        # back up original XML file
        try:
            copyfile(base_xml_file, base_xml_file+".back")
        except IOError as err:
            log.error(err)
            return
        except:
            log.error("Unable to copy target file "+base_xml_file)
            return

        # comment tags
        if tags_to_comment:
            for tag in tags_to_comment:
                self.comment_tag(tag, xml_doc)

        # add tags
        if tags_to_add:
            self.tags_hash = compute_tags_hash(tags_to_add)
            self.hash_duplicate = find_hash_in_comments(xml_doc, self.tags_hash)
            if self.hash_duplicate and self.DUPLICATE_HASH_ACTION == DuplicateHashAction.SKIP:
                log.info("Skipping tags_to_add: identical content hash already exists in file.")
            else:
                for tag in tags_to_add:
                    self.add_tag(tag, xml_doc)

        # give write permission to file (save original permissions)
        original_mode = os.stat(base_xml_file).st_mode
        os.chmod(base_xml_file, stat.S_IWRITE)

        # write changes to file
        with open(base_xml_file, 'wb') as f:
            f.write(xml_doc.toxml(encoding="UTF-8"))

        # restore original permissions
        os.chmod(base_xml_file, original_mode)

    def update_xml_with_patterns(
        self,
        base_xml_file: str,
        tag_patterns_to_comment: List[str] = [],
        tags_to_comment: List[str] = [],
        tags_to_add: List[str] = []
    ):
        """
        Update an XML file with tags to add, exact tags to comment, and tag patterns to comment.

        Args:
            base_xml_file: Path to the base XML file to modify
            tag_patterns_to_comment: List of tag name patterns/prefixes to comment (e.g., ['security:ssl-forward-cert-verify-2'])
            tags_to_comment: List of exact XML tags to comment
            tags_to_add: List of XML tags to add
        """
        # BugFix: Reset instance state flags to allow reuse of XMLManipulator across multiple files
        self.comment_added = False
        self.tags_hash = None
        self.hash_duplicate = False
        
        if not tags_to_add and not tags_to_comment and not tag_patterns_to_comment:
            log.error("Should specify either tags to add, tags to comment, or tag patterns to comment.")
            return

        # Parse XML file into a minidom.Document
        xml_doc = minidom.parse(base_xml_file)

        # Back up original XML file
        try:
            copyfile(base_xml_file, base_xml_file + ".back")
        except IOError as err:
            log.error(err)
            return
        except:
            log.error("Unable to copy target file " + base_xml_file)
            return

        # Comment tags by pattern
        if tag_patterns_to_comment:
            for pattern in tag_patterns_to_comment:
                self.comment_tags_by_pattern(pattern, xml_doc)

        # Comment exact tags
        if tags_to_comment:
            for tag in tags_to_comment:
                self.comment_tag(tag, xml_doc)

        # Add tags
        if tags_to_add:
            self.tags_hash = compute_tags_hash(tags_to_add)
            self.hash_duplicate = find_hash_in_comments(xml_doc, self.tags_hash)
            if self.hash_duplicate and self.DUPLICATE_HASH_ACTION == DuplicateHashAction.SKIP:
                log.info("Skipping tags_to_add: identical content hash already exists in file.")
            else:
                for tag in tags_to_add:
                    self.add_tag(tag, xml_doc)

        # Give write permission to file (save original permissions)
        original_mode = os.stat(base_xml_file).st_mode
        os.chmod(base_xml_file, stat.S_IWRITE)

        # Write changes to file
        with open(base_xml_file, 'wb') as f:
            f.write(xml_doc.toxml(encoding="UTF-8"))

        # Restore original permissions
        os.chmod(base_xml_file, original_mode)

    def update_xml_with_template(self, filepath: str):
        """Update the target XML file with a tags from a Template File

        Args:
            filepath: Path to the Template File
        """
        with open(filepath) as f:
            config = ast.literal_eval(f.read())

        for base_xml_file in config:
            # get the tags to be updated
            tags_to_add = None
            tags_to_comment = None
            if "tags_to_add" in config[base_xml_file]:
                tags_to_add = config[base_xml_file]["tags_to_add"]
            if "tags_to_comment" in config[base_xml_file]:
                tags_to_comment = config[base_xml_file]["tags_to_comment"]

            self.update_xml(base_xml_file, tags_to_comment, tags_to_add)

    def add_child_tag_to_element(
        self,
        xml_file: str,
        parent_element_pattern: str,
        child_tags: List[str],
        check_duplicates: bool = True
    ) -> bool:
        """
        Add child tags inside an existing parent element using text-based manipulation.

        This method preserves XML formatting and avoids namespace pollution (ns0:, etc.)
        that occurs with DOM-based parsers when dealing with namespaced XML.

        Args:
            xml_file: Path to the XML file to modify
            parent_element_pattern: Regex pattern to find the parent element
                                   Example: r'<trident:include[^>]*file="myfile.xml"[^>]*>'
            child_tags: List of child tags to add (e.g., ['<host>example.com</host>'])
            check_duplicates: If True, skip tags that already exist in the parent

        Returns:
            True if successful, False otherwise

        Example:
            # Add a host to an arlindex.xml include entry
            manipulator.add_child_tag_to_element(
                xml_file='/path/to/arlindex.xml',
                parent_element_pattern=r'<trident:include[^>]*file="myarl.xml"[^>]*>',
                child_tags=['<host>newhost.com</host>']
            )
        """
        try:
            # Read the entire XML file as text
            with open(xml_file, 'r', encoding='utf-8') as f:
                xml_content = f.read()

            # Build the full pattern to find parent element and its content
            # Pattern: (<opening tag>)(content)(</closing tag>)
            # Extract tag name from the opening tag pattern
            import re

            # Find the opening tag using the provided pattern
            opening_match = re.search(parent_element_pattern, xml_content)
            if not opening_match:
                log.warning(f"Parent element not found matching pattern: {parent_element_pattern}")
                return False

            opening_tag = opening_match.group(0)

            # Extract the tag name to find the closing tag
            tag_name_match = re.search(r'<([^\s:>]+):([^\s>]+)', opening_tag)
            if tag_name_match:
                # Namespaced tag (e.g., trident:include)
                namespace = tag_name_match.group(1)
                tag_name = tag_name_match.group(2)
                closing_tag_pattern = f"</{namespace}:{tag_name}>"
            else:
                # Simple tag
                tag_name_match = re.search(r'<([^\s/>]+)', opening_tag)
                if not tag_name_match:
                    log.error("Could not extract tag name from opening tag")
                    return False
                tag_name = tag_name_match.group(1)
                closing_tag_pattern = f"</{tag_name}>"

            # Build pattern to capture the entire element
            # Need to find the matching closing tag considering nesting
            opening_pos = opening_match.start()

            # Find all closing tags after the opening tag
            closing_matches = list(re.finditer(re.escape(closing_tag_pattern), xml_content[opening_pos:]))
            if not closing_matches:
                log.error(f"No closing tag found for {closing_tag_pattern}")
                return False

            # Count nested tags to find the matching closing tag
            search_text = xml_content[opening_pos:]
            open_count = 1
            pos = len(opening_tag)

            while pos < len(search_text) and open_count > 0:
                # Look for next opening or closing tag
                next_open = search_text.find(opening_tag, pos)
                next_close = search_text.find(closing_tag_pattern, pos)

                if next_close == -1:
                    log.error("Could not find matching closing tag")
                    return False

                if next_open != -1 and next_open < next_close:
                    # Found another opening tag (nested)
                    open_count += 1
                    pos = next_open + len(opening_tag)
                else:
                    # Found a closing tag
                    open_count -= 1
                    if open_count == 0:
                        # This is the matching closing tag
                        closing_pos = opening_pos + next_close
                        break
                    pos = next_close + len(closing_tag_pattern)

            if open_count != 0:
                log.error("Could not find matching closing tag")
                return False

            # Extract the element content (between opening and closing tags)
            element_start = opening_pos + len(opening_tag)
            element_content = xml_content[element_start:closing_pos]

            # Process each child tag to add
            modified_content = element_content
            for child_tag in child_tags:
                # Extract the tag value if checking duplicates
                if check_duplicates:
                    # Parse the child tag to get its content
                    child_match = re.search(r'<([^>]+)>(.*?)</\1>', child_tag)
                    if child_match:
                        child_tag_name = child_match.group(1).split()[0]  # Get tag name without attributes
                        child_value = child_match.group(2)

                        # Check if this value already exists in the element
                        if re.search(rf'<{re.escape(child_tag_name)}[^>]*>{re.escape(child_value)}</{re.escape(child_tag_name)}>', element_content):
                            log.info(f"Child tag with value '{child_value}' already exists, skipping")
                            continue

                # Determine indentation by looking at existing child elements
                indent_match = re.search(r'\n(\s+)<', element_content)
                indent = indent_match.group(1) if indent_match else '          '

                # Add the new child tag with proper indentation
                new_child_line = f"\n{indent}{child_tag}"
                modified_content += new_child_line
                log.info(f"Added child tag: {child_tag}")

            # Reconstruct the element with modified content
            new_element = opening_tag + modified_content + closing_tag_pattern

            # Replace the old element with the new one in the XML
            old_element = xml_content[opening_pos:closing_pos + len(closing_tag_pattern)]
            xml_content = xml_content.replace(old_element, new_element, 1)

            # Write back the modified XML (preserve original file permissions)
            original_mode = os.stat(xml_file).st_mode
            os.chmod(xml_file, stat.S_IWRITE)

            with open(xml_file, 'w', encoding='utf-8') as f:
                f.write(xml_content)

            # Restore original permissions
            os.chmod(xml_file, original_mode)

            log.info(f"Successfully added child tags to {xml_file}")
            return True

        except Exception as e:
            log.error(f"Failed to add child tags: {e}")
            import traceback
            log.debug(traceback.format_exc())
            return False
