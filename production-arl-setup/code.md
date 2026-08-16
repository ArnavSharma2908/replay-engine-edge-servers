Let's rewrite the whole project. The project is given a hostname and CPCode. Install the customer configuration on Trident network.
Edge-> and Parent.

1. All customer configurations are present at a server and a location. -> Should be in config
2. We can get input as (cpcode, hostname)(used to search the ARL file) or the ARL file directly. Other inputs are the servers for eg: --edge <edge_ip> --parent <parent_ip>
3. We need to find the right config.
    a) Search in the given location for the CPCode. For eg: "1608524" look for this tag: <reporting:cpcode>1608524</reporting:cpcode>
    b) If only 1 config is returned that is our config otherwise, we look which file has the hostname: For eg: hubdoc-app.xero-uat.com
4.  Or we can be given the ARL file directly. For eg: ARL_hubdoc-app.xero.com.1042719.xml
5. Once we got the config, Copy ARL file to edge and parent machines using scp

To setup Edge:

1. Update the file /a/etc/ghost/arl.data/arlindex.xml which is a mapping of host to ARL file on the edge. This tells ghost to use which config for which hostname. One entry looks like:

  <trident:include type="arldata" file="ARL_hubdoc-app.xero.com.1042719.xml">
      <host>hubdoc-app.xero-uat.com</host>
  </trident:include>

you can look at: root@198.18.134.246:~# vim /a/etc/ghost/arl.data/arlindex.xml to see the structure of this file.

2. To force the edge to go forward to the parent, we can configure the ARL file to always go to the parent:
This needs to at end of the customer config file ARL_hubdoc-app.xero.com.1042719.xml
    <!-- simulate a dns lookup -->
    <network:dns.simulate-lookup>
       <status>on</status>
       <addr><PARENT_IP></addr>
    </network:dns.simulate-lookup>

3. To update configuration files ARL files or arlindex.xml, we already have a utility xml-manipulator


Doing this might not be sufficient to do the setup. We have to do active checking if this worked
Checking if it worked has 2 steps:
1. Running a manual curl command given a sample request that worked
2. Checking access logs for that request. Access logs have r and f-line. We need to make sure we see an f-line on the edge, f-line has forward-ip of the parent machine. 

you can look at access log at: /a/logs/ghost.access.log
f 1781707037.868 - 0 0 1 0 271 456 198.18.134.247 198.18.134.246 GET /D/16382/1/000/origin.yap.trident.com/statuscode/file_500k.html?sim200 404 text/html - - oaDxW 8 - - cab18a7d origin.yap.trident.com 555 555 - /D/16382/1/000/origin.yap.trident.com/statuscode/file_500k.htmlstatuscode/file_500k.html?sim200 74c - - 140 140 - - - - - - - 0;-;d;0;-;d;1 0-511999 r7f3f14f1f050.-8.p.g - - 350 954d4880 - 0 0 8I - - - mtse=g|0;pin=http|-;ssl=0::::;netp= - 0.UNDEFINED 0 - NS:NS::: - - edge.yap.trident.com

1.   Record tag: 'f' for forward
2.   Start time: The time immediately prior to deciding whether to perform a DNS lookup on the desired server.
3.   SSL overhead time: Time elapsed for the SSL handshake. Non-SSLv connections will log a time of '-'. This time is included in the request-end-time delta.
4.   Request end time: The delta between when the DNS lookup completed until the last request byte was written. SSL overhead is included in this time; DNS time is not.
5.   DNS lookup time: The delta between the start of the request and the completion of the DNS lookup, if one was required. Some of the time ghost will have a cached IP address and this field will be zero.
6.   Turnaround time: The delta between when the last request byte was written and when first reply byte was received from the forward server (origin server, peer, or parent).
7.   Transfer time: The difference between receipt of the first byte of the reply and the last byte of the reply.
8.   Content bytes received: The size of the actual content data transferred from the server. This includes chunked encoding overhead if present.
9.   Total bytes received: Total bytes for this transaction, including reply headers. This does not include any out-of-band (retransmit, fragmentation, or IP overhead) estimates.
10.   Forward IP: The dotted-quad IPv4 or the IPv6 address we received the content from. In certain cases this will the first 15 characters of the hostname rather than the IP address.
If ghost could not look up the IP address for the host in DNS. In this case the corresponding 'r' line will record ERR_DNS_FAIL.
If ghost could not connect to that IP address. In this case the corresponding 'r' line will record ERR_CONNECT_FAIL.
If the content provider was not authorized to use ghost. In this case the corresponding 'r' line will record ERR_ACCESS_DENIED.
If ghost did not receive any content from the origin before the connection was terminated.
'Aborted' in this field means the forward connection was aborted before ghost decided whether it should go to a peer or the origin. The 'r' line may indicate the reason for the forward abort, possibly a client abort.
11.   Client IP: The address for which we initiated this connection, if known. If unknown, '-'. For prefresh connections, 'p'. The address can be a dotted-quad IPv4 address, or an IPv6 address (eight groups of four hexadecimal characters separated by colons; likely reduced as described here: https://docs.trident.com/initiative/ipv6/kf1804/kf1804-design--ipv6-for-http.xml#2.2)
12.   HTTP method: The request method the client sent to ghost (GET, HEAD, POST).
13.   ARL: The Trident Resource Locator from the client's request, possibly rewritten after applying metadata. This should be the same as the ARL logged in the response line (see the discussion above about typecodes). Logging of query string can be controlled by log-query-string and query-string-filter metadata in the same way as mentioned in r12.
14.   HTTP status code: The HTTP result code we received from the server, even if the forward connection is terminated before ghost receives the full object. If there was an error receiving the object, ghost 4.3 and later will note that in the object status field ('e' or 'f' codes). More information regarding the particular error may be available from the cache.logs file. For details, see the "Forward Failures" document at https://docs.trident.com/esp/user/edgesuite/ForwardFailures.htm . Any non-standard status codes will be logged in f-14 (as well as in r-13) for any request that has bypass enabled by customers.
15.   Content-Type: The Content-Type of the object we received from the server. Note that 304 Not Modified responses to IMS requests almost never have a Content-Type header.
16.   IMS: 'i' if the ghost request included the HTTP If-Modified-Since: header, making the request a conditional GET, or '-' for non-IMS requests.
17.   SSL: The SSL version number if SSL was used, '-' otherwise. For example, 'TLSv1'.
18.   Object status: This field contains the following tags based upon the upstream request destination (and in some cases the request). You'll find an alphabetized listing below.

and many more but fields that concern us are in this details

3. If we don't see the correct f-line we need to update the customer ARL, add more files to make this to work and after every step we need to do the test again

4. The idea is to have a rule file which will contain the different things we can try: The rule file will have actions
1. actions: copy(file or directory)(at edge or at parent or at peer), modify xml (at edge, at parent or at peer).



reason:"r-line-error: ''"
fix: copy : {
    source: ""
    destination
}


We will have to write more actions as we encounter issues, but I hope you got the gist. 

Please ask me questions if you have any. This current repo was the first attempt at this project. 