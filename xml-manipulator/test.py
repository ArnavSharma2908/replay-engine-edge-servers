# Unit Testing for the XML Manipulator Library

import unittest
import os
from xmlmaniplib.xml_manipulator import XMLManipulator

current_dir = os.getcwd()

class TestAddTagMethod(unittest.TestCase):
    def setUp(self):
        self.metadata_file = current_dir+"/testfiles/baseline-hacks.xml"
        self.xml_file = open(self.metadata_file, "r")
        self.original_file_content = self.xml_file.read()
        self.backup = current_dir+"/testfiles/baseline-hacks.xml.back"
        self.xml_manip = XMLManipulator()

    def test_simple_tag(self):
        tags_to_add = ["<config:rollback.use-certificate-channel>off</config:rollback.use-certificate-channel>"]

        # check that xml file does not contain the tag initially
        self.assertNotIn(tags_to_add[0], self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_add=tags_to_add)
        
        # check that the xml file contains the added tag
        new_file_content = self.xml_file.read()
        self.assertIn(tags_to_add[0], new_file_content)

        # check if backup file was created
        self.assertTrue(os.path.exists(self.backup))

    def test_multiple_tags(self):
        tags_to_add = ["<config:rollback.use-certificate-channel-1>off</config:rollback.use-certificate-channel-1>", 
                       "<security:ssl.service-ip-fallback-shared-cert>on</security:ssl.service-ip-fallback-shared-cert>"]

        # check that xml file does not contain the tag initially
        self.assertNotIn(tags_to_add[0], self.original_file_content)
        self.assertNotIn(tags_to_add[1], self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_add=tags_to_add)
        
        # check that the xml file contains the added tag
        new_file_content = self.xml_file.read()
        self.assertIn(tags_to_add[0], new_file_content)
        self.assertIn(tags_to_add[1], new_file_content)

    def test_tag_with_attributes(self):
        tags_to_add = ["<security:ssl.service-ip-fallback-shared-cert type='attribute'>on</security:ssl.service-ip-fallback-shared-cert>",
                       "<config:rollback.use-certificate-channel version='2'>off</config:rollback.use-certificate-channel>",]

        # check that xml file does not contain the tag initially
        self.assertNotIn(tags_to_add[0], self.original_file_content)
        self.assertNotIn(tags_to_add[1], self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_add=tags_to_add)
        
        # check that the xml file contains the added tag
        new_file_content = self.xml_file.read()
        self.assertIn("<security:ssl.service-ip-fallback-shared-cert type=\"attribute\">on</security:ssl.service-ip-fallback-shared-cert>", new_file_content)
        self.assertIn("<config:rollback.use-certificate-channel version=\"2\">off</config:rollback.use-certificate-channel>", new_file_content)

    def test_tag_with_multiple_attributes(self):
        tags_to_add = ["<security:ssl.service-ip-fallback-shared-cert type='attribute' version='2'>on</security:ssl.service-ip-fallback-shared-cert>"]

        # check that xml file does not contain the tag initially
        self.assertNotIn("<security:ssl.service-ip-fallback-shared-cert type=\"attribute\" version=\"2\">on</security:ssl.service-ip-fallback-shared-cert>", self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_add=tags_to_add)
        
        # check that the xml file contains the added tag
        new_file_content = self.xml_file.read()
        self.assertIn("<security:ssl.service-ip-fallback-shared-cert type=\"attribute\" version=\"2\">on</security:ssl.service-ip-fallback-shared-cert>", new_file_content)

    def test_nested_tags(self):
        tags_to_add = ["<security:ssl.server-session-caching.max-early-data>", 
                       "<advertised>4096B</advertised>",
                       "<hello>",
                       "<name>4096B</name>",
                       "<hello2>",
                       "<name2>4096B</name2>",
                       "</hello2>",
                       "</hello>",
                       "<receivable>16384B</receivable>",
                       "</security:ssl.server-session-caching.max-early-data>"]

        # check that xml file does not contain the tag initially
        self.assertNotIn(tags_to_add[0], self.original_file_content)
        self.assertNotIn(tags_to_add[1], self.original_file_content)
        self.assertNotIn(tags_to_add[2], self.original_file_content)
        self.assertNotIn(tags_to_add[3], self.original_file_content)
        self.assertNotIn(tags_to_add[4], self.original_file_content)
        self.assertNotIn(tags_to_add[5], self.original_file_content)
        self.assertNotIn(tags_to_add[6], self.original_file_content)
        self.assertNotIn(tags_to_add[7], self.original_file_content)
        self.assertNotIn(tags_to_add[8], self.original_file_content)
        self.assertNotIn(tags_to_add[9], self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_add=tags_to_add)
        
        # check that the xml file contains the added tag
        new_file_content = self.xml_file.read()
        self.assertIn(tags_to_add[0], new_file_content)
        self.assertIn(tags_to_add[1], new_file_content)
        self.assertIn(tags_to_add[2], new_file_content)
        self.assertIn(tags_to_add[3], new_file_content)
        self.assertIn(tags_to_add[4], new_file_content)
        self.assertIn(tags_to_add[5], new_file_content)
        self.assertIn(tags_to_add[6], new_file_content)
        self.assertIn(tags_to_add[7], new_file_content)
        self.assertIn(tags_to_add[8], new_file_content)
        self.assertIn(tags_to_add[9], new_file_content)

    def tearDown(self):
        # restore original file
        self.xml_file.close()
        self.xml_file = open(self.metadata_file, "w")
        self.xml_file.write(self.original_file_content)
        self.xml_file.close()

        # remove backup file
        if os.path.exists(self.backup):
            os.remove(self.backup)

class TestCommentTagMethod(unittest.TestCase):
    def setUp(self):
        self.metadata_file = current_dir+"/testfiles/ghost.xml"
        self.xml_file = open(self.metadata_file, "r")
        self.original_file_content = self.xml_file.read()
        self.tags_to_add = None
        self.backup = current_dir+"/testfiles/ghost.xml.back"
        self.xml_manip = XMLManipulator()

    def test_simple_tag(self):
        tags_to_comment = ["<cache:uvm.cache-size>400</cache:uvm.cache-size>"]
        commented_text = "<!--"+tags_to_comment[0]+"-->"

        # check that xml file does not contain the tag commented initially
        self.assertNotIn(commented_text, self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_comment=tags_to_comment)
        
        # check that the xml file contains the added tag
        new_xml_file = open(self.metadata_file, "r")
        new_file_content = new_xml_file.read()
        new_xml_file.close()
        self.assertIn(commented_text, new_file_content)

        # check if backup file was created
        self.assertTrue(os.path.exists(self.backup))

    def test_multiple_tags(self):
        tags_to_comment = ["<cache:uvm.max-cache-size>400</cache:uvm.max-cache-size>",
                           "<cache:uvm.initial-cache-size>300</cache:uvm.initial-cache-size>"]
        commented_text_0 = "<!--"+tags_to_comment[0]+"-->"
        commented_text_1 = "<!--"+tags_to_comment[1]+"-->"

        # check that xml file does not contain the tag commented initially
        self.assertNotIn(commented_text_0, self.original_file_content)
        self.assertNotIn(commented_text_1, self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_comment=tags_to_comment)
        
        # check that the xml file contains the added tag
        new_xml_file = open(self.metadata_file, "r")
        new_file_content = new_xml_file.read()
        new_xml_file.close()
        self.assertIn(commented_text_0, new_file_content)
        self.assertIn(commented_text_1, new_file_content)

    def test_tags_with_subtags(self):
        tags_to_comment = ["<config:metadata.suspend-on-startup>"]
        comment_start = "<!--<config:metadata.suspend-on-startup>"
        comment_end = "</config:metadata.suspend-on-startup>-->"

        # check that xml file does not contain the tag commented initially
        self.assertNotIn(comment_start, self.original_file_content)
        self.assertNotIn(comment_end, self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_comment=tags_to_comment)
        
        # check that the xml file contains the added tag
        new_xml_file = open(self.metadata_file, "r")
        new_file_content = new_xml_file.read()
        new_xml_file.close()
        self.assertIn(comment_start, new_file_content)
        self.assertIn(comment_end, new_file_content)

    def test_tag_with_attribute(self):
        tags_to_comment = ["<edgecomputing:system.service name='EdgeJava'>"]
        commented_text = "<!--<edgecomputing:system.service name=\"EdgeJava\">"

        # check that xml file does not contain the tag commented initially
        self.assertNotIn(commented_text, self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_comment=tags_to_comment)
        
        # check that the xml file contains the added tag
        new_xml_file = open(self.metadata_file, "r")
        new_file_content = new_xml_file.read()
        new_xml_file.close()
        self.assertIn(commented_text, new_file_content)

    def test_tag_with_multiple_attributes(self):
        tags_to_comment = ["<trident:include type='appsecthr' file='appsecthrindex.xml'/>"]
        commented_text = "<!--<trident:include type=\"appsecthr\" file=\"appsecthrindex.xml\"/>-->"

        # check that xml file does not contain the tag commented initially
        self.assertNotIn(commented_text, self.original_file_content)

        self.xml_manip.update_xml(self.metadata_file, tags_to_comment=tags_to_comment)
        
        # check that the xml file contains the added tag
        new_xml_file = open(self.metadata_file, "r")
        new_file_content = new_xml_file.read()
        new_xml_file.close()
        self.assertIn(commented_text, new_file_content)

    def tearDown(self):
        # restore original file
        self.xml_file.close()
        self.xml_file = open(self.metadata_file, "w")
        self.xml_file.write(self.original_file_content)
        self.xml_file.close()

        # remove backup file
        if os.path.exists(self.backup):
            os.remove(self.backup)

class TestInvalidInputs(unittest.TestCase):
    def setUp(self):
        self.metadata_file = current_dir+"/testfiles/ghost.xml"
        self.xml_manip = XMLManipulator()

    def test_invalid_tag_format(self):
        tags_to_comment = ["config:rollback.use-certificate-channel> off</config:rollback.use-certificate-channel>",       # missing <
                           "<security:ssl.service-ip-fallback-shared-cert",                                                # missing >
                           "<>off</config:rollback.use-certificate-channel>",                                              # missing tag name
                           ]
        tags_to_add = ["<config:rollback.use-certificate-channel>off",                                                     # missing second half
                       "config:rollback.use-certificate-channel> off</config:rollback.use-certificate-channel>",           # missing <
                       "<security:ssl.service-ip-fallback-shared-cert  on</security:ssl.service-ip-fallback-shared-cert>", # missing >
                       "<>off</config:rollback.use-certificate-channel>",                                                  # missing tag name
                       "<config:rollback.use-certificate-channel></config:rollback.use-certificate-channel>",              # missing tag text
                       ]

        with self.assertLogs() as captured:
            self.xml_manip.update_xml(self.metadata_file, tags_to_comment, tags_to_add)

        # check number of log messages and their contents
        self.assertEqual(len(captured.records), 8) 
        self.assertEqual(captured.records[0].getMessage(), "Tag \""+tags_to_comment[0]+"\" is in incorrect format (comment tag).")
        self.assertEqual(captured.records[1].getMessage(), "Tag \""+tags_to_comment[1]+"\" is in incorrect format (comment tag).")
        self.assertEqual(captured.records[2].getMessage(), "Tag \""+tags_to_comment[2]+"\" is in incorrect format (comment tag).")
        self.assertEqual(captured.records[3].getMessage(), "Tag \""+tags_to_add[0]+"\" is in incorrect format (add tag).")
        self.assertEqual(captured.records[4].getMessage(), "Tag \""+tags_to_add[1]+"\" is in incorrect format (add tag).")
        self.assertEqual(captured.records[5].getMessage(), "Tag \""+tags_to_add[2]+"\" is in incorrect format (add tag).")
        self.assertEqual(captured.records[6].getMessage(), "Tag \""+tags_to_add[3]+"\" is in incorrect format (add tag).")
        self.assertEqual(captured.records[7].getMessage(), "Tag \""+tags_to_add[4]+"\" is in incorrect format (add tag).")
        
    def test_invalid_attribute_format(self):
        tags = ["<config:rollback.use-certificate-channel version'2'>off</config:rollback.use-certificate-channel>", # missing =
                "<config:rollback.use-certificate-channel ='2'>off</config:rollback.use-certificate-channel>",       # missing attribute name
                "<config:rollback.use-certificate-channel version=>off</config:rollback.use-certificate-channel>",   # missing attribute value
                ]
                       
        with self.assertLogs() as captured:
            self.xml_manip.update_xml(self.metadata_file, tags, tags)

        # check number of log messages and their contents
        self.assertEqual(len(captured.records), 6)
        self.assertEqual(captured.records[0].getMessage(), "Attributes in tag \""+tags[0]+"\" are in incorrect format (comment tag).")
        self.assertEqual(captured.records[1].getMessage(), "Attributes in tag \""+tags[1]+"\" are in incorrect format (comment tag).")
        self.assertEqual(captured.records[2].getMessage(), "Attributes in tag \""+tags[2]+"\" are in incorrect format (comment tag).") 
        self.assertEqual(captured.records[3].getMessage(), "Attributes in tag \""+tags[0]+"\" are in incorrect format (add tag).")
        self.assertEqual(captured.records[4].getMessage(), "Attributes in tag \""+tags[1]+"\" are in incorrect format (add tag).")
        self.assertEqual(captured.records[5].getMessage(), "Attributes in tag \""+tags[2]+"\" are in incorrect format (add tag).")

    def test_tag_does_not_exist(self):
        tags_to_comment = ["<does-not-exist>400</does-not-exist>",
                           "<trident:include type='attribute'/>",
                           "<cache:uvm.cache-size>4</cache:uvm.cache-size>"]
        tags_to_add = None

        with self.assertLogs() as captured:
            self.xml_manip.update_xml(self.metadata_file, tags_to_comment, tags_to_add)

        # check number of log messages and their contents
        self.assertEqual(len(captured.records), 3) 
        self.assertEqual(captured.records[0].getMessage(), "Attempting to comment tag \"does-not-exist\" that does not exist in file.")
        self.assertEqual(captured.records[1].getMessage(), "Tag \""+tags_to_comment[1]+"\" with specified attributes/text was not found.")
        self.assertEqual(captured.records[2].getMessage(), "Tag \""+tags_to_comment[2]+"\" with specified attributes/text was not found.")

    def test_add_to_invalid_header_file(self):
        metadata_file = current_dir+"/testfiles/invalid_format_file.xml"
        tags_to_comment = None
        tags_to_add = ["<cache:uvm.cache-size>400</cache:uvm.cache-size>"]

        with self.assertLogs() as captured:
            self.xml_manip.update_xml(metadata_file, tags_to_comment, tags_to_add)

        # check if backup file was created
        self.assertTrue(os.path.exists(current_dir+"/testfiles/invalid_format_file.xml.back"))

        # check number of log messages and their contents
        self.assertEqual(len(captured.records), 1) 
        self.assertEqual(captured.records[0].getMessage(), "Unable to add tag to the provided file because the format is not supported.")

        # remove backup file
        if os.path.exists(metadata_file+".back"):
            os.remove(metadata_file+".back")

    def test_empty_tags(self):
        with self.assertLogs() as captured:
            self.xml_manip.update_xml(self.metadata_file, tags_to_add=[])

        self.assertEqual(captured.records[0].getMessage(), "Should specify either tags to add or tags to comment.")


    def tearDown(self):
        # remove backup file
        if os.path.exists(self.metadata_file+".back"):
            os.remove(self.metadata_file+".back")

class TestUpdateWithTemplateMethod(unittest.TestCase):
    def test_template_file(self):
        template_file = current_dir+"/sample_template_file.py"
        commented_tag = "<!--<cache:uvm.cache-size>400</cache:uvm.cache-size>-->"
        added_tag = "<config:rollback.use-certificate-channel>on</config:rollback.use-certificate-channel>"
        metadata_file = current_dir+"/testfiles/ghost.xml"
        xml_manip = XMLManipulator()

        # check that xml file does not contain the tags commented/added initially
        xml_file = open(metadata_file, "r")
        original_file_content = xml_file.read()
        xml_file.close()
        self.assertNotIn(commented_tag, original_file_content)
        self.assertNotIn(added_tag, original_file_content)

        xml_manip.update_xml_with_template(template_file)
        
        # check that the xml file contains the added tag
        new_xml_file = open(metadata_file, "r")
        new_file_content = new_xml_file.read()
        new_xml_file.close()
        self.assertIn(commented_tag, new_file_content)
        self.assertIn(added_tag, new_file_content)

        # restore original file
        xml_file = open(metadata_file, "w")
        xml_file.write(original_file_content)
        xml_file.close()

        # remove backup file
        if os.path.exists(metadata_file+".back"):
            os.remove(metadata_file+".back")

class TestDuplicateHashDetection(unittest.TestCase):
    """Test case for duplicate content hash detection (DUPLICATE_HASH_ACTION=0)"""
    def setUp(self):
        self.metadata_file = current_dir+"/testfiles/baseline-hacks.xml"
        self.xml_file = open(self.metadata_file, "r")
        self.original_file_content = self.xml_file.read()
        self.backup = current_dir+"/testfiles/baseline-hacks.xml.back"
        self.xml_manip = XMLManipulator()
        # Ensure DUPLICATE_HASH_ACTION is set to 0 (skip silently on duplicate)
        XMLManipulator.DUPLICATE_HASH_ACTION = 0

    def test_duplicate_hash_skips_tag_addition(self):
        """First run adds tag with hash comment; second run detects duplicate and skips"""
        tags_to_add = ["<config:rollback.use-certificate-channel>off</config:rollback.use-certificate-channel>"]
        
        # First run: add tags (no duplicate yet)
        self.xml_manip.update_xml(self.metadata_file, tags_to_add=tags_to_add)
        
        # Read file after first run
        xml_file = open(self.metadata_file, "r")
        content_after_first = xml_file.read()
        xml_file.close()
        
        # Verify first run added the tag with hash comment
        self.assertIn("Content hash:", content_after_first)
        self.assertIn(tags_to_add[0], content_after_first)
        
        # Second run: add same tags (duplicate exists, should skip)
        self.xml_manip.update_xml(self.metadata_file, tags_to_add=tags_to_add)
        
        # Read file after second run
        xml_file = open(self.metadata_file, "r")
        content_after_second = xml_file.read()
        xml_file.close()
        
        # Count how many times the tag appears (should be exactly 1, not duplicated)
        tag_count = content_after_second.count(tags_to_add[0])
        self.assertEqual(tag_count, 1, "Tag should appear exactly once; duplicate was not skipped")
        
        # Verify no warning comment was added (DUPLICATE_HASH_ACTION=0 means silent skip)
        self.assertNotIn("Warning: An identical content hash", content_after_second)

    def test_different_tags_dont_trigger_duplicate(self):
        """Different tag sets should not conflict; each should be added with unique hash"""
        tags_set_1 = ["<config:rollback.use-certificate-channel>off</config:rollback.use-certificate-channel>"]
        tags_set_2 = ["<security:ssl.service-ip-fallback-shared-cert>on</security:ssl.service-ip-fallback-shared-cert>"]
        
        # Add first set
        self.xml_manip.update_xml(self.metadata_file, tags_to_add=tags_set_1)
        
        # Create new manipulator for second run
        manipulator2 = XMLManipulator()
        XMLManipulator.DUPLICATE_HASH_ACTION = 0
        manipulator2.update_xml(self.metadata_file, tags_to_add=tags_set_2)
        
        # Both tags should be present (different hashes, no conflict)
        xml_file = open(self.metadata_file, "r")
        content = xml_file.read()
        xml_file.close()
        
        self.assertIn(tags_set_1[0], content)
        self.assertIn(tags_set_2[0], content)

    def tearDown(self):
        # Restore original file
        self.xml_file.close()
        self.xml_file = open(self.metadata_file, "w")
        self.xml_file.write(self.original_file_content)
        self.xml_file.close()

        # Remove backup file
        if os.path.exists(self.backup):
            os.remove(self.backup)
        
        # Reset DUPLICATE_HASH_ACTION to default
        XMLManipulator.DUPLICATE_HASH_ACTION = 0

if __name__ == '__main__':
    unittest.main(verbosity=2)
