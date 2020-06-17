#
# README FIRST
#
# Uncomment the following line to install required packages
# install.packages("zoo","xts","stringr","fs","readr","jsonlite")
#
# Load libraries
library(zoo)
library(stringr)
library(fs)
library(xts)
library(readr)
library(jsonlite)
#
# General options
#
# File encoding in UTF-8
options("encoding" = "UTF-8")
#
# Download latest stations file and convert it to JSON
#
urlhead = "https://dev.azure.com/tankerkoenig/362e70d1-bafa-4cf7-a346-1f3613304973/_apis/git/repositories/0d6e7286-91e4-402c-af56-fa75be1f223d/items?path=%2Fstations%2F2020%2F06%2F"
urltail = "-stations.csv&versionDescriptor%5BversionOptions%5D=0&versionDescriptor%5BversionType%5D=0&versionDescriptor%5Bversion%5D=master&resolveLfs=true&api-version=5.0"
url = paste0(urlhead, (Sys.Date() -1 ) ,urltail)
#url
#d = read_csv(url)
#d$openingtimes_json=NULL
#d$first_active=NULL
#json = toJSON(d, dataframe="rows")
#write(json, "data/stations.json")
#
# Download latest price updates and calculate savings per time interval
#
# Calculate Files to download
# End of URL
urltail = "-prices.csv&versionDescriptor%5BversionOptions%5D=0&versionDescriptor%5BversionType%5D=0&versionDescriptor%5Bversion%5D=master&resolveLfs=true&%24format=octetStream&api-version=5.0&download=true"
# Start of URL
urlhead ="https://dev.azure.com/tankerkoenig/362e70d1-bafa-4cf7-a346-1f3613304973/_apis/git/repositories/0d6e7286-91e4-402c-af56-fa75be1f223d/items?path=%2Fprices%2F"
# Load Data from yesterday to get last price update before midnight
date = Sys.Date()-1
year = format(date,"%Y")
month = format(date,"%m")
day = format(date,"%d")
# Calculate pattern of file on Azure GIT repository
file = paste0(year,"%2F", month, "%2F", year, "-", month, "-", day )
# Calculate full URL of file to download
url=paste0(urlhead, file, urltail)
# Load file from Web
data=read.csv(url)
# Load Data for yesterday to get last price updates, see comments above
daysfrom = Sys.Date()-8
daysto = Sys.Date()-2
daysrange = seq(daysfrom,daysto,by=1)
for(date in daysrange) {
  
  date = as.Date(date, origin = "1970-01-01")
  year = format(date,"%Y")
  month = format(date,"%m")
  day = format(date,"%d")
  file = paste0(year,"%2F", month, "%2F", year, "-", month, "-", day )
  url=paste0(urlhead, file, urltail)
  d2 = read.csv(url)
  # Concatenate data sets
  data = rbind(data,d2)
  cat(paste0("Obtained Price Updates: ", nrow(data),"\n"))
}
daysto = Sys.Date()-1
daysfrom= Sys.Date()-7
# Find all stations providing price updates
stations= unique(data$station_uuid)
#
#
#  Loop over stations and create files
#
#
for(s in stations) {
  # Log progress
  cat(paste0("Processing ", s, "...\n"))
  # Attempt to use data for this station
  try({
    # Look only at data of this station
    station = subset(data, station_uuid==s) 
    # Convert date and time to R format
    station$date = as.POSIXlt(station$date, format="%Y-%m-%d %H:%M:%OS")
    # Calculate Savings for yesterday
    start = as.POSIXlt(paste0(daysfrom, " 0:00:00"))
    end = as.POSIXlt(paste0(daysto, " 23:59:59"))
    # Find the last price update day prior to date of interes
    last = subset(station, date < start)
    last = last$date[nrow(last)]
    # Get only data relevant for yesterday
    station = subset(station, date >= last)
    # Regularize time to 1 Minute intervals
    ts.1min <- seq(last,end, by = paste0("60 s"))
    #
    # regularize Diesel
    #
    x = xts(x=station$diesel , order.by=station$date, name="Diesel")
    # Carry forward updates on a per minute basis to calculate correct averages
    maxabs = max(x)
    minabs = min(x)
    #cat(paste0(s,":",minabs,"-",maxabs," for Diesel\n"))
    # Carry forward
    res <- merge(x, xts(, ts.1min))
    res <- na.locf(res, na.rm = TRUE)
    res <- window(x = res,start = start, end= end)
    # Aggregate by hour and calculate average
    ends <- endpoints(res,'minutes',60)
    table =  period.apply(res, ends ,mean)
    table = table-mean(res)  # abs. savings in cents rounded to two digits
    # Create new data structure for aggregation
    table = data.frame(date=index(table), coredata(table))
    table$hour = format(table$date, "%H")
    table$date = NULL
    names(table) = c("price","hour")
    # Calculate average by hour
    result <- tapply(table$price, table$hour, mean)
    # Prepare data frame contents for file writing from tapply result
    result.frame = data.frame(key=names(result), value=result)
    names(result.frame)  = c("hour","price")
    result.frame[,2] = round (  result.frame[,2] ,2) # Show only two Digits
    result.frame$hour = as.numeric(result.frame$hour)-1
    result.frame =  result.frame[order( result.frame$hour), ]
    # Write File and create required directories
    dirname=path_join(str_split(station$station_uuid[1],"-"))
    dirname=path_join(c(path("data2"), dirname))
    filename=path("diesel.csv")
    dir_create(dirname, recurse=T)
    filename=path_join(c(dirname,filename))
    # Write as CSV
    write.csv(result.frame, filename, row.names=F)
    # Write as JSON
    filename=path("diesel.json")
    filename=path_join(c(dirname,filename))
    json = toJSON(result.frame, dataframe="rows")
    min = min(result.frame$price)
    max = max(result.frame$price)
    span = max - min
    mins = subset(result.frame, price == min)
    # summarize min_frame_hour to text
    rstart = mins$hour[1]
    rend = rstart+1    
    bt_txt = ""
    
    if(nrow(mins) > 1 ) {
    for (h in mins$hour[2:nrow(mins)]) {
      if (h==rend) { 
        rend = rend+1
      } else {
        bt_txt = paste0(bt_txt, rstart, " - ", rend, "h, ")
        rstart = h
        rend = h +1
      }
    }
      }
    bt_txt = paste0(bt_txt, rstart, " - ", rend, "h")
    json = paste0('{"hourly":',json, ',"text":"', bt_txt,'","besthours":', toJSON(mins$hour),',"min":', min, ',"max":', max, ',"minabs":', minabs, ',"maxabs":', maxabs, ',"span":', span, '}')
    #cat(json)
    write(json,filename)
    #
    # E10
    #
    # Comments see Diesel
    x = xts(x=station$e10 , order.by=station$date, name="E10")
    res <- merge(x, xts(, ts.1min))
    res <- na.locf(res, na.rm = TRUE)
    res <- window(x = res,start = start, end= end)
    ends <- endpoints(res,'minutes',60)
    table =  period.apply(res, ends ,mean)
    maxabs = max(x)
    minabs = min(x)
    table = table-mean(res)  # abs. savings in cents rounded to two digits
    #cat(paste0(s,":",minabs,"-",maxabs," for E10\n"))
    table = data.frame(date=index(table), coredata(table))
    table$hour = format(table$date, "%H")
    table$date = NULL
    names(table) = c("price","hour")
    result <- tapply(table$price, table$hour, mean)
    result.frame = data.frame(key=names(result), value=result)
    names(result.frame)  = c("hour","price")
    result.frame$hour = as.numeric(result.frame$hour)-1
    result.frame[,2] = round (  result.frame[,2] ,2) # Show only two Digits
    # Write File
    dirname=path_join(str_split(station$station_uuid[1],"-"))
    dirname=path_join(c(path("data2"), dirname))
    filename=path("e10.csv")
    dir_create(dirname, recurse=T)
    filename=path_join(c(dirname,filename))
    write.csv(result.frame, filename, row.names=F)
    # Write as JSON
    filename=path("e10.json")
    filename=path_join(c(dirname,filename))
    json = toJSON(result.frame, dataframe="rows")
    min = min(result.frame$price)
    max = max(result.frame$price)
    span = max - min
    mins = subset(result.frame, price == min)
    # summarize min_frame_hour to text
    rstart = mins$hour[1]
    rend = rstart+1    
    bt_txt = ""
    if(nrow(mins) > 1 ) {
    for (h in mins$hour[2:nrow(mins)]) {
      if (h==rend) { 
        rend = rend+1
      } else {
        bt_txt = paste0(bt_txt, rstart, " - ", rend, "h, ")
        rstart = h
        rend = h +1
      }
    }
    }
    bt_txt = paste0(bt_txt, rstart, " - ", rend, "h")
    json = paste0('{"hourly":',json, ',"text":"', bt_txt,'","besthours":', toJSON(mins$hour),',"min":', min, ',"max":', max, ',"minabs":', minabs, ',"maxabs":', maxabs, ',"span":', span, '}')
    #cat(json)
    write(json, filename)
    #
    # E5
    #
    # Comments see Diesel
    x = xts(x=station$e5 , order.by=station$date, name="E5\n")    
    maxabs = max(x)
    minabs = min(x)
    res <- merge(x, xts(, ts.1min))
    res <- na.locf(res, na.rm = TRUE)
    res <- window(x = res,start = start, end= end)
    # Aggregate by hour and calculate average
    ends <- endpoints(res,'minutes',60)
    table =  period.apply(res, ends ,mean)
    table = table-mean(res)  # abs. savings in cents rounded to two digits
    #cat(paste0(s,":",minabs,"-",maxabs," for E5\n\n"))
    table = data.frame(date=index(table), coredata(table))
    table$hour = format(table$date, "%H")
    table$date = NULL
    names(table) = c("price","hour")
    result <- tapply(table$price, table$hour, mean)
    result.frame = data.frame(key=names(result), value=result)
    names(result.frame)  = c("hour","price")
    result.frame$hour = as.numeric(result.frame$hour)-1
    result.frame[,2] = round (  result.frame[,2] ,2) # Show only two Digits
    result.frame =  result.frame[order( result.frame$hour), ]
    # Write File
    dirname=path_join(str_split(station$station_uuid[1],"-"))
    dirname=path_join(c(path("data2"), dirname))
    filename=path("e5.csv")
    dir_create(dirname, recurse=T)
    filename=path_join(c(dirname,filename))
    write.csv(result.frame, filename, row.names=F)    
    # Write as JSON
    filename=path("e5.json")
    filename=path_join(c(dirname,filename))
    json = toJSON(result.frame, dataframe="rows")
    min = min(result.frame$price)
    max = max(result.frame$price)
    span = max - min
    mins = subset(result.frame, price == min)
 # summarize min_frame_hour to text
    rstart = mins$hour[1]
    rend = rstart+1    
    bt_txt = ""
    
    if(nrow(mins) > 1 ) {
    for (h in mins$hour[2:nrow(mins)]) {
      if (h==rend) { 
        rend = rend+1
      } else {
        bt_txt = paste0(bt_txt, rstart, " - ", rend, "h, ")
        rstart = h
        rend = h +1
      }
    }
      }
    bt_txt = paste0(bt_txt, rstart, " - ", rend, "h")
    json = paste0('{"hourly":',json, ',"text":"', bt_txt,'","besthours":', toJSON(mins$hour),',"min":', min, ',"max":', max, ',"minabs":', minabs, ',"maxabs":', maxabs, ',"span":', span, '}')
      #cat(json)
    write(json, filename)
  }) # END TRY
} # END FOR LOOP