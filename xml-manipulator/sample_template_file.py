{
    # full path of file to be updated
    "testfiles/ghost.xml" : {
        "tags_to_comment" : [
            # list tags to be commented out (optional)
            r"<cache:uvm.cache-size>400</cache:uvm.cache-size>"
        ],
        "tags_to_add" : [
            # list tags to be added (optional)
            r"<config:rollback.use-certificate-channel>on</config:rollback.use-certificate-channel>",
        ]
    }
}
