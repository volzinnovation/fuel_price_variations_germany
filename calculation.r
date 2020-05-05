library(zoo)
library(stringr)
library(fs)
library(xts)
# File path
urltail = "-prices.csv&versionDescriptor%5BversionOptions%5D=0&versionDescriptor%5BversionType%5D=0&versionDescriptor%5Bversion%5D=master&resolveLfs=true&%24format=octetStream&api-version=5.0&download=true"
urlhead ="https://dev.azure.com/tankerkoenig/362e70d1-bafa-4cf7-a346-1f3613304973/_apis/git/repositories/0d6e7286-91e4-402c-af56-fa75be1f223d/items?path=%2Fprices%2F"
#file = "2020%2F05%2F2020-05-04"
# Load Data from two days ago to get last price update before midnight
date=Sys.Date()-3
year = format(date,"%Y")
month = format(date,"%m")
day = format(date,"%d")
file = paste0(year,"%2F", month, "%2F", year, "-", month, "-", day )
url=paste0(urlhead, file, urltail)
d=read.csv(url)
# Load Data for yesterday to get last price updates
date=Sys.Date()-2
year = format(date,"%Y")
month = format(date,"%m")
day = format(date,"%d")
file = paste0(year,"%2F", month, "%2F", year, "-", month, "-", day )
url=paste0(urlhead, file, urltail)
d2 = read.csv(url)
# Concatenate data sets
data = rbind(d,d2)
stations= unique(data$station_uuid)
# 0d58c4ba-3267-404a-89d6-6ba15a8fc422 Station
#
# TO DO Loop over stations
#
#

## WITHIN FOR LOOP 
for(s in stations) {
  cat(paste0("Processing ", s, "...\n"))
  try({
station = subset(data, station_uuid==s)
station$date = as.POSIXlt(station$date, format="%Y-%m-%d %H:%M:%OS")
# Calculate Savings for last day
start = as.POSIXlt(paste0(date, " 0:00:00"))
end = as.POSIXlt(paste0(date, " 23:59:59"))
last = subset(station, date < start)
last = last$date[nrow(last)]
station = subset(station, date >= last)
# Object for regularization of time
ts.1min <- seq(last,end, by = paste0("60 s"))
#
# regularize Diesel
#
x = xts(x=station$diesel , order.by=station$date, name="Diesel")
res <- merge(x, xts(, ts.1min))
res <- na.locf(res, na.rm = TRUE)
res <- window(x = res,start = start, end= end)
ends <- endpoints(res,'minutes',60)
table =  period.apply(res, ends ,mean)-mean(res)  # abs. savings in cents rounded to two digits
table = data.frame(date=index(table), coredata(table))
table$hour = format(table$date, "%H")
table$date = NULL
names(table) = c("price","hour")
result <- tapply(table$price, table$hour, mean)
result.frame = data.frame(key=names(result), value=result)
names(result.frame)  = c("hour","price")
result.frame[,2] = round (  result.frame[,2] ,2) # Show only two Digits
result.frame =  result.frame[order( result.frame$hour), ]
# Write File
dirname=path_join(str_split(station$station_uuid[1],"-"))
dirname=path_join(c(path("data"), dirname))
filename=path("diesel.csv")
dir_create(dirname, recursive=T)
filename=path_join(c(dirname,filename))
write.csv(result.frame, filename, row.names=F)
#
# E10
#
x = xts(x=station$e10 , order.by=station$date, name="E10")
res <- merge(x, xts(, ts.1min))
res <- na.locf(res, na.rm = TRUE)
res <- window(x = res,start = start, end= end)
ends <- endpoints(res,'minutes',60)
table =  period.apply(res, ends ,mean)-mean(res)  # abs. savings in cents rounded to two digits
table = data.frame(date=index(table), coredata(table))
table$hour = format(table$date, "%H")
table$date = NULL
names(table) = c("price","hour")
result <- tapply(table$price, table$hour, mean)
result.frame = data.frame(key=names(result), value=result)
names(result.frame)  = c("hour","price")
result.frame[,2] = round (  result.frame[,2] ,2) # Show only two Digits
# Write File
dirname=path_join(str_split(station$station_uuid[1],"-"))
dirname=path_join(c(path("data"), dirname))
filename=path("e10.csv")
dir_create(dirname, recursive=T)
filename=path_join(c(dirname,filename))
write.csv(result.frame, filename, row.names=F)
#
# E5
#
x = xts(x=station$e5 , order.by=station$date, name="E5")
res <- merge(x, xts(, ts.1min))
res <- na.locf(res, na.rm = TRUE)
res <- window(x = res,start = start, end= end)
ends <- endpoints(res,'minutes',60)
table =  period.apply(res, ends ,mean)-mean(res)  # abs. savings in cents rounded to two digits
table = data.frame(date=index(table), coredata(table))
table$hour = format(table$date, "%H")
table$date = NULL
names(table) = c("price","hour")
result <- tapply(table$price, table$hour, mean)
result.frame = data.frame(key=names(result), value=result)
names(result.frame)  = c("hour","price")
result.frame[,2] = round (  result.frame[,2] ,2) # Show only two Digits
result.frame =  result.frame[order( result.frame$hour), ]
# Write File
dirname=path_join(str_split(station$station_uuid[1],"-"))
dirname=path_join(c(path("data"), dirname))
filename=path("e5.csv")
dir_create(dirname, recursive=T)
filename=path_join(c(dirname,filename))
write.csv(result.frame, filename, row.names=F)
    }) # TRY
}